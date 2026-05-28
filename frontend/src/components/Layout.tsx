// App shell: sidebar nav + main content. Loads current user once.

import { useEffect, useState } from 'react'

function getCsrfToken(): string {
  return document.cookie.split('; ').find(r => r.startsWith('csrftoken='))?.split('=')[1] ?? ''
}
import { NavLink, Outlet } from 'react-router-dom'
import { api } from '../api'
import type { CurrentUser } from '../types'

export function Layout() {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch((e) => setLoadError(e.message))
  }, [])

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">Breathe ESG</div>
        <nav>
          <NavLink to="/uploads" className={({ isActive }) => (isActive ? 'active' : '')}>
            Uploads
          </NavLink>
          <NavLink to="/review" className={({ isActive }) => (isActive ? 'active' : '')}>
            Review queue
          </NavLink>
        </nav>
        <div className="user">
          {user ? (
            <>
              <div className="name">{user.email}</div>
              <div>{user.organization.name}</div>
              <div style={{ marginTop: 8 }}>
                <form method="post" action="/admin/logout/" style={{ display: 'inline' }}>
                  <input type="hidden" name="csrfmiddlewaretoken" value={getCsrfToken()} />
                  <button type="submit" style={{ background: 'none', border: 'none', padding: 0, color: 'inherit', cursor: 'pointer', textDecoration: 'underline', fontSize: 'inherit' }}>
                    Log out
                  </button>
                </form>
              </div>
            </>
          ) : loadError ? (
            <div>error: {loadError}</div>
          ) : (
            <div>loading…</div>
          )}
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}
