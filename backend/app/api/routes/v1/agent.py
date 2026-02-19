"""AI Agent WebSocket routes with streaming support (LangChain)."""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langchain.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage

from app.agents.langchain_assistant import AgentContext, get_agent
from app.api.deps import get_conversation_service
from app.db.session import get_db_context
from app.schemas.conversation import (
    ConversationCreate,
    MessageCreate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class AgentConnectionManager:
    """WebSocket connection manager for AI agent."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and store a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Agent WebSocket connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(
            f"Agent WebSocket disconnected. Total connections: {len(self.active_connections)}"
        )

    async def send_event(self, websocket: WebSocket, event_type: str, data: Any) -> bool:
        """Send a JSON event to a specific WebSocket client.

        Returns True if sent successfully, False if connection is closed.
        """
        try:
            await websocket.send_json({"type": event_type, "data": data})
            return True
        except (WebSocketDisconnect, RuntimeError):
            # Connection already closed
            return False


manager = AgentConnectionManager()


def build_message_history(
    history: list[dict[str, str]],
) -> list[HumanMessage | AIMessage | SystemMessage]:
    """Convert conversation history to LangChain message format."""
    messages: list[HumanMessage | AIMessage | SystemMessage] = []

    for msg in history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
        elif msg["role"] == "system":
            messages.append(SystemMessage(content=msg["content"]))

    return messages


@router.websocket("/ws/agent")
async def agent_websocket(
    websocket: WebSocket,
) -> None:
    """WebSocket endpoint for AI agent with streaming support.

    Uses LangChain stream() to stream agent events including:
    - user_prompt: When user input is received
    - text_delta: Streaming text from the model
    - tool_call: When a tool is called
    - tool_result: When a tool returns a result
    - final_result: When the final result is ready
    - complete: When processing is complete
    - error: When an error occurs

    Expected input message format:
    {
        "message": "user message here",
        "history": [{"role": "user|assistant|system", "content": "..."}],
        "conversation_id": "optional-uuid-to-continue-existing-conversation"
    }

    Persistence: Set 'conversation_id' to continue an existing conversation.
    If not provided, a new conversation is created. The conversation_id is
    returned in the 'conversation_created' event.
    """

    await manager.connect(websocket)

    # Conversation state per connection
    conversation_history: list[dict[str, str]] = []
    context: AgentContext = {}
    current_conversation_id: str | None = None

    try:
        while True:
            # Receive user message
            data = await websocket.receive_json()
            user_message = data.get("message", "")
            # Optionally accept history from client (or use server-side tracking)
            if "history" in data:
                conversation_history = data["history"]

            if not user_message:
                await manager.send_event(websocket, "error", {"message": "Empty message"})
                continue

            # Handle conversation persistence
            try:
                async with get_db_context() as db:
                    conv_service = get_conversation_service(db)

                    # Get or create conversation
                    requested_conv_id = data.get("conversation_id")
                    if requested_conv_id:
                        current_conversation_id = requested_conv_id
                        # Verify conversation exists
                        await conv_service.get_conversation(UUID(requested_conv_id))
                    elif not current_conversation_id:
                        # Create new conversation
                        conv_data = ConversationCreate(
                            title=user_message[:50] if len(user_message) > 50 else user_message,
                        )
                        conversation = await conv_service.create_conversation(conv_data)
                        current_conversation_id = str(conversation.id)
                        await manager.send_event(
                            websocket,
                            "conversation_created",
                            {"conversation_id": current_conversation_id},
                        )

                    # Save user message
                    await conv_service.add_message(
                        UUID(current_conversation_id),
                        MessageCreate(role="user", content=user_message),
                    )
            except Exception as e:
                logger.warning(f"Failed to persist conversation: {e}")
                # Continue without persistence

            await manager.send_event(websocket, "user_prompt", {"content": user_message})

            try:
                assistant = get_agent()
                model_history = build_message_history(conversation_history)
                model_history.append(HumanMessage(content=user_message))

                final_output = ""
                tool_events: list[Any] = []
                seen_tool_call_ids: set[str] = set()

                await manager.send_event(websocket, "model_request_start", {})

                for stream_mode, data in assistant.agent.stream(
                    {"messages": model_history},
                    stream_mode=["messages", "updates"],
                    config={"configurable": context} if context else None,
                ):
                    if stream_mode == "messages":
                        token, metadata = data

                        if isinstance(token, AIMessageChunk):
                            if token.content:
                                text_content = ""
                                if isinstance(token.content, str):
                                    text_content = token.content
                                elif isinstance(token.content, list):
                                    for block in token.content:
                                        if isinstance(block, dict) and block.get("type") == "text":
                                            text_content += block.get("text", "")
                                        elif isinstance(block, str):
                                            text_content += block

                                if text_content:
                                    await manager.send_event(
                                        websocket,
                                        "text_delta",
                                        {"content": text_content},
                                    )
                                    final_output += text_content

                            if token.tool_call_chunks:
                                for tc_chunk in token.tool_call_chunks:
                                    tc_id = tc_chunk.get("id")
                                    tc_name = tc_chunk.get("name")
                                    if tc_id and tc_name and tc_id not in seen_tool_call_ids:
                                        seen_tool_call_ids.add(tc_id)
                                        await manager.send_event(
                                            websocket,
                                            "tool_call",
                                            {
                                                "tool_name": tc_name,
                                                "args": {},
                                                "tool_call_id": tc_id,
                                            },
                                        )

                    elif stream_mode == "updates":
                        for node_name, update in data.items():
                            if node_name == "tools":
                                for msg in update.get("messages", []):
                                    if isinstance(msg, ToolMessage):
                                        await manager.send_event(
                                            websocket,
                                            "tool_result",
                                            {
                                                "tool_call_id": msg.tool_call_id,
                                                "content": msg.content,
                                            },
                                        )
                            elif node_name == "model":
                                for msg in update.get("messages", []):
                                    if isinstance(msg, AIMessage) and msg.tool_calls:
                                        for tc in msg.tool_calls:
                                            tc_id = tc.get("id", "")
                                            if tc_id not in seen_tool_call_ids:
                                                seen_tool_call_ids.add(tc_id)
                                                tool_events.append(tc)
                                                await manager.send_event(
                                                    websocket,
                                                    "tool_call",
                                                    {
                                                        "tool_name": tc.get("name", ""),
                                                        "args": tc.get("args", {}),
                                                        "tool_call_id": tc_id,
                                                    },
                                                )

                await manager.send_event(
                    websocket,
                    "final_result",
                    {"output": final_output},
                )

                # Update conversation history
                conversation_history.append({"role": "user", "content": user_message})
                if final_output:
                    conversation_history.append({"role": "assistant", "content": final_output})

                # Save assistant response to database
                if current_conversation_id and final_output:
                    try:
                        async with get_db_context() as db:
                            conv_service = get_conversation_service(db)
                            await conv_service.add_message(
                                UUID(current_conversation_id),
                                MessageCreate(
                                    role="assistant",
                                    content=final_output,
                                    model_name=assistant.model_name
                                    if hasattr(assistant, "model_name")
                                    else None,
                                ),
                            )
                    except Exception as e:
                        logger.warning(f"Failed to persist assistant response: {e}")

                await manager.send_event(
                    websocket,
                    "complete",
                    {
                        "conversation_id": current_conversation_id,
                    },
                )

            except WebSocketDisconnect:
                # Client disconnected during processing - this is normal
                logger.info("Client disconnected during agent processing")
                break
            except Exception as e:
                logger.exception(f"Error processing agent request: {e}")
                # Try to send error, but don't fail if connection is closed
                await manager.send_event(websocket, "error", {"message": str(e)})

    except WebSocketDisconnect:
        pass  # Normal disconnect
    finally:
        manager.disconnect(websocket)
