import { toast as sonnerToast } from "sonner";

interface ToastOptions {
  title: string;
  description?: string;
  variant?: "default" | "destructive";
}

function toast({ title, description, variant }: ToastOptions) {
  const message = description ? `${title}: ${description}` : title;
  if (variant === "destructive") {
    sonnerToast.error(message);
  } else {
    sonnerToast.success(message);
  }
}

export function useToast() {
  return { toast };
}
