import { useEffect, useRef } from "react";
import { CONFIRM_DEFAULT_KEY } from "../../constants/storage";

interface Props {
  title: string;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
  confirmLabel?: string;
  danger?: boolean;
  defaultFocus?: "cancel" | "confirm";
}

export default function ConfirmDialog({ title, message, onConfirm, onCancel, confirmLabel = "Confirm", danger = false, defaultFocus }: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const focusConfirm =
      defaultFocus === "confirm" ||
      (defaultFocus === undefined && danger && localStorage.getItem(CONFIRM_DEFAULT_KEY) === "confirm");
    if (focusConfirm) {
      confirmRef.current?.focus();
    } else {
      cancelRef.current?.focus();
    }
  }, []);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      if (document.activeElement === cancelRef.current) {
        confirmRef.current?.focus();
      } else {
        cancelRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="card p-6 w-full max-w-sm space-y-4">
        <h3 className="font-semibold text-lg">{title}</h3>
        <p className="text-gray-400 text-sm">{message}</p>
        <div className="flex gap-3 justify-end">
          <button ref={cancelRef} className="btn-ghost" onClick={onCancel}>Cancel</button>
          <button ref={confirmRef} className={danger ? "btn-danger" : "btn-primary"} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
