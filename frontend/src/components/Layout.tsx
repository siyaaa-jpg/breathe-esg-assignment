// App shell: sidebar nav + main content. Loads current user once.

import { useEffect, useState } from 'react'
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
                <a href="/admin/logout/?next=/">Log out</a>
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
