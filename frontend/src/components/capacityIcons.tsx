// Minimal forward-facing line icons for the stress-test capacity status
// (tiger: fierce face — angled brows, stripes, fangs; rabbit: round face, tall ears),
// drawn for this site in the 24×24 / 1.5px-stroke style of the rest of the UI.
// Stroke/fill come from the parent <svg> (stroke="currentColor", fill="none").

export function TigerIcon() {
  return (
    <>
      <path d="M12 3.6C8.3 3.6 5.9 5.4 5.2 8.4L3.6 10.9l1.5.9-1.2 2.6 2 .8C6.9 18.5 9.2 20.4 12 20.4s5.1-1.9 6.1-5.2l2-.8-1.2-2.6 1.5-.9-1.6-2.5C18.1 5.4 15.7 3.6 12 3.6z" />
      <path d="M6.3 6.4 5.6 3.5l3 1.1M17.7 6.4l.7-2.9-3 1.1" />
      <path d="M12 4.4v2.1M10 4.9l.7 1.5M14 4.9l-.7 1.5" />
      <path d="M7.6 9.6l3 1.4M16.4 9.6l-3 1.4" />
      <path d="M8.5 11.9c.8.5 1.6.5 2.2.1M15.5 11.9c-.8.5-1.6.5-2.2.1" />
      <path d="M10.9 13.5h2.2L12 14.7z" fill="currentColor" />
      <path d="M12 14.7v.8M9.4 16c1.6 1.5 3.6 1.5 5.2 0" />
      <path d="M10.3 16.5l.3 1.2M13.7 16.5l-.3 1.2" />
      <path d="M5.4 12.7l1.7.2M18.6 12.7l-1.7.2" />
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
