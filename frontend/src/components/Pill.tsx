// Tiny single-purpose components. Classes resolve in styles.css.

export function StatusPill({ status }: { status: string }) {
  return <span className={`pill pill-${status}`}>{status}</span>
}

export function FlagChips({ flags }: { flags: string[] }) {
  if (!flags || flags.length === 0) return null
  return (
    <>
      {flags.map((f) => (
        <span key={f} className="flag-chip">
          {f}
        </span>
      ))}
    </>
  )
}
