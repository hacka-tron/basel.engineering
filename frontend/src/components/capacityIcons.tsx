// Minimal forward-facing line icons for the stress-test capacity status
// (lion: mane arcing over a shield-shaped face; rabbit: round face, tall ears),
// drawn for this site in the 24×24 / 1.5px-stroke style of the rest of the UI.
// Stroke/fill come from the parent <svg> (stroke="currentColor", fill="none").

export function LionIcon() {
  return (
    <>
      <path d="M5.2 16.5C3.2 12.5 3.6 6.4 8 4.3c2.4-1.2 5.6-1.2 8 0 4.4 2.1 4.8 8.2 2.8 12.2" />
      <path d="M5.2 16.5l1.6 1.2M18.8 16.5l-1.6 1.2M3.9 11.5l1.4.3M20.1 11.5l-1.4.3" />
      <path d="M8 10.5C8 8 16 8 16 10.5V14c0 3.3-2.4 5.3-4 5.3S8 17.3 8 14z" />
      <path d="M8.3 9.6a1.4 1.4 0 0 1 2.3-1.5" />
      <path d="M15.7 9.6a1.4 1.4 0 0 0-2.3-1.5" />
      <circle cx="10.3" cy="12.2" r=".6" fill="currentColor" stroke="none" />
      <circle cx="13.7" cy="12.2" r=".6" fill="currentColor" stroke="none" />
      <path d="M11.2 14.3h1.6L12 15.2z" fill="currentColor" />
      <path d="M12 15.2v.8M12 16c-.5.5-1.2.5-1.7.1M12 16c.5.5 1.2.5 1.7.1" />
    </>
  )
}

export function RabbitIcon() {
  return (
    <>
      <path d="M9.6 8.2C8.4 6 8.3 2.6 9.3 2.2s2.3 2.7 2.1 5.6" />
      <path d="M14.4 8.2c1.2-2.2 1.3-5.6.3-6s-2.3 2.7-2.1 5.6" />
      <circle cx="12" cy="13.2" r="5.6" />
      <circle cx="10.1" cy="12.4" r=".55" fill="currentColor" stroke="none" />
      <circle cx="13.9" cy="12.4" r=".55" fill="currentColor" stroke="none" />
      <path d="M11.3 14.3h1.4L12 15z" fill="currentColor" />
      <path d="M12 15v.7M12 15.7c-.45.45-1.1.45-1.5.1M12 15.7c.45.45 1.1.45 1.5.1" />
    </>
  )
}
