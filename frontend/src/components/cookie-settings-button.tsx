"use client";

type CookieSettingsButtonProps = {
  className?: string;
  label?: string;
};

export function CookieSettingsButton({ className, label = "Cookie settings" }: CookieSettingsButtonProps) {
  return (
    <button
      type="button"
      className={className}
      onClick={() => {
        if (typeof window !== "undefined") {
          window.dispatchEvent(new Event("helvex-open-cookie-banner"));
        }
      }}
    >
      {label}
    </button>
  );
}
