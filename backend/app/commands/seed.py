# ruff: noqa: I001 - Imports structured for Jinja2 template conditionals
"""
Seed database with sample data.

This command is useful for development and testing.
Uses random data generation - install faker for better data:
    uv add faker --group dev
"""

import asyncio

import random
import secrets

import click

from sqlalchemy import delete, select


from app.commands import command, info, success, warning

# Try to import Faker for better data generation
try:
    from faker import Faker

    fake = Faker()
    HAS_FAKER = True
except ImportError:
    HAS_FAKER = False
    fake = None


def random_title() -> str:
    """Generate a random item title."""
    if HAS_FAKER:
        return fake.sentence(nb_words=4).rstrip(".")
    adjectives = ["Amazing", "Great", "Awesome", "Fantastic", "Incredible", "Beautiful"]
    nouns = ["Widget", "Gadget", "Thing", "Product", "Item", "Object"]
    return f"{random.choice(adjectives)} {random.choice(nouns)}"


def random_description() -> str:
    """Generate a random description."""
    if HAS_FAKER:
        return fake.paragraph(nb_sentences=3)
    return "This is a sample description for development purposes."


DEFAULT_SERVICE_API_KEY = "sk_live_" + secrets.token_hex(16)


@command("seed", help="Seed database with sample data")
@click.option("--count", "-c", default=10, type=int, help="Number of item records to create")
@click.option(
    "--clear/--no-clear",
    default=True,
    help="Clear all existing users before seeding (default: True)",
)
@click.option("--dry-run", is_flag=True, help="Show what would be created without making changes")
@click.option("--items/--no-items", default=True, help="Seed items (default: True)")
@click.option(
    "--admin-email",
    default="admin@dispatch-chicago.com",
    show_default=True,
    help="Superadmin email",
)
@click.option(
    "--admin-password",
    default="admin123",
    show_default=True,
    help="Superadmin password (dev only — override for non-dev)",
)
@click.option(
    "--user-email",
    default="dispatch@example.com",
    show_default=True,
    help="Regular user email",
)
@click.option(
    "--user-password",
    default="password123",
    show_default=True,
    help="Regular user password (dev only — override for non-dev)",
)
@click.option(
    "--service-account-name",
    default="WhatsApp Extension",
    show_default=True,
    help="Name for the seeded service account (the Chrome extension)",
)
@click.option(
    "--service-api-key",
    default=DEFAULT_SERVICE_API_KEY,
    show_default=True,
    help="API key for the service account (sk_live_ + 32 hex). "
    "Visible to anyone with source access — dev only.",
)
def seed(
    count: int,
    clear: bool,
    dry_run: bool,
    items: bool,
    admin_email: str,
    admin_password: str,
    user_email: str,
    user_password: str,
    service_account_name: str,
    service_api_key: str,
) -> None:
    """
    Seed the database with sample data for development.

    Creates two human users (one superadmin, one regular operator) and one
    service account for the WhatsApp Chrome extension. With --clear (default),
    every existing user row is deleted first, so the extension's existing
    service accounts are removed and the new key below replaces them — paste
    the printed key into the extension popup on next launch.

    Example:
        project cmd seed --dry-run
        project cmd seed --no-clear                      # idempotent
        project cmd seed --admin-password secret
        project cmd seed --service-api-key sk_live_...  # custom dev key
    """
    if not HAS_FAKER:
        warning(
            "Faker not installed. Using basic random data. For better data: uv add faker --group dev"
        )

    if dry_run:
        info("[DRY RUN] Would create 2 human users:")
        info(f"  - superadmin: {admin_email}")
        info(f"  - user:       {user_email}")
        info("[DRY RUN] Would upsert service account:")
        info(f"  - {service_account_name} (api key prefix {service_api_key[:12]}…)")
        if clear:
            info("[DRY RUN] Would clear ALL existing users first")
        if items:
            info(f"[DRY RUN] Would create {count} sample items")
        return
    from app.core.security import get_password_hash, hash_api_key
    from app.db.models.user import User
    from app.db.models.item import Item
    from app.db.session import async_session_maker

    if not (service_api_key.startswith("sk_live_") and len(service_api_key) == 40):
        raise click.ClickException(
            f"--service-api-key must be 'sk_live_' + 32 hex chars "
            f"(got {len(service_api_key)} chars)"
        )

    async def _seed():
        async with async_session_maker() as session:
            if clear:
                info("Clearing all existing users...")
                result = await session.execute(delete(User))
                await session.commit()
                info(f"Deleted {result.rowcount} users")

            async def _upsert_user(
                email: str,
                password: str,
                full_name: str,
                is_superuser: bool,
                role: str,
            ) -> str:
                existing = (
                    (await session.execute(select(User).where(User.email == email)))
                    .scalars()
                    .first()
                )
                if existing is not None:
                    existing.hashed_password = get_password_hash(password)
                    existing.full_name = full_name
                    existing.is_active = True
                    existing.is_superuser = is_superuser
                    existing.role = role
                    return "updated"
                session.add(
                    User(
                        email=email,
                        hashed_password=get_password_hash(password),
                        full_name=full_name,
                        is_active=True,
                        is_superuser=is_superuser,
                        role=role,
                    )
                )
                return "created"

            admin_action = await _upsert_user(
                email=admin_email,
                password=admin_password,
                full_name="Admin",
                is_superuser=True,
                role="admin",
            )
            user_action = await _upsert_user(
                email=user_email,
                password=user_password,
                full_name="Dispatch Operator",
                is_superuser=False,
                role="user",
            )

            key_hash = hash_api_key(service_api_key)
            key_prefix = service_api_key[:12]
            existing_svc = (
                (
                    await session.execute(
                        select(User).where(
                            User.is_service_account.is_(True),
                            User.service_account_name == service_account_name,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if existing_svc is not None:
                existing_svc.service_api_key_hash = key_hash
                existing_svc.service_api_key_prefix = key_prefix
                existing_svc.is_active = True
                svc_action = "updated"
            else:
                import secrets as _secrets

                placeholder_email = f"svc-{_secrets.token_hex(8)}@service.local"
                session.add(
                    User(
                        email=placeholder_email,
                        hashed_password=None,
                        full_name=service_account_name,
                        is_active=True,
                        is_superuser=False,
                        role="user",
                        is_service_account=True,
                        service_api_key_hash=key_hash,
                        service_api_key_prefix=key_prefix,
                        service_account_name=service_account_name,
                    )
                )
                svc_action = "created"

            await session.commit()
            success(
                f"superadmin {admin_email}: {admin_action}; "
                f"user {user_email}: {user_action}; "
                f"service account '{service_account_name}': {svc_action}"
            )

            click.echo("")
            click.secho("=" * 72, fg="green")
            click.secho(
                f"  Service account: {service_account_name} ({svc_action})",
                fg="green",
                bold=True,
            )
            click.secho(
                "  Paste this API key into the Chrome extension popup:",
                fg="yellow",
                bold=True,
            )
            click.echo("")
            click.secho(f"    {service_api_key}", fg="white", bg="red", bold=True)
            click.echo("")
            click.secho(
                "  The key is also printed above by --dry-run. It is stored",
                fg="green",
            )
            click.secho(
                "  only as a bcrypt hash — re-running seed with the same",
                fg="green",
            )
            click.secho(
                "  --service-api-key leaves the hash unchanged.",
                fg="green",
            )
            click.secho("=" * 72, fg="green")

            if items:
                result = await session.execute(select(Item).limit(1))
                existing = result.scalars().first()
                if existing:
                    info(
                        "Items already exist. Pass --clear (default) plus delete manually to reseed items."
                    )
                else:
                    info(f"Creating {count} sample items...")
                    for _ in range(count):
                        item = Item(
                            title=random_title(),
                            description=random_description(),
                            is_active=random.choice([True, True, True, False]),
                        )
                        session.add(item)
                    await session.commit()
                    success(f"Created {count} items")

    asyncio.run(_seed())
