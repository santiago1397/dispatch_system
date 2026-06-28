import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export interface SpinnerProps extends React.HTMLAttributes<HTMLSpanElement> {
  size?: "sm" | "md" | "lg";
}

const sizeClasses: Record<NonNullable<SpinnerProps["size"]>, string> = {
  sm: "h-4 w-4",
  md: "h-5 w-5",
  lg: "h-8 w-8",
};

function Spinner({ className, size = "md", ...props }: SpinnerProps) {
  return (
    <span
      role="status"
      aria-live="polite"
      aria-label="Loading"
      className={cn("inline-flex", className)}
      {...props}
    >
      <Loader2 className={cn("animate-spin", sizeClasses[size])} aria-hidden="true" />
    </span>
  );
}

export { Spinner };