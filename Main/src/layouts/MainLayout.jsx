import React, { useState, useEffect } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from '../components/Layout/Sidebar'
import Topbar from '../components/Layout/Topbar'
import AIWidget from '../components/Dashboard/AIWidget'
import './MainLayout.css'
import FloatingAskAIButton from '../components/Common/FloatingAskAIButton'

/**
 * MainLayout
 *
 * 3-column responsive structure:
 * 1. Sidebar — fixed width on desktop, fixed drawer on mobile (≤1024px)
 * 2. Main Content — fluid
 * 3. AI Assistant Widget — right column on desktop, hidden on mobile
 */
export default function MainLayout() {
  const location = useLocation()
  const [sidebarOpen, setSidebarOpen] = useState(false)

  const showAIWidget = ['/', '/dashboard', '/sops'].includes(location.pathname)

  // Close drawer on route change
  useEffect(() => {
    setSidebarOpen(false)
  }, [location.pathname])

  // Prevent body scroll when sidebar drawer is open
  useEffect(() => {
    document.body.style.overflow = sidebarOpen ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [sidebarOpen])

  return (
    <div className="main-layout-container">
      <Sidebar isOpen={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <main className="main-content-area">
        <Topbar onMenuToggle={() => setSidebarOpen(prev => !prev)} />
        <div className="page-outlet">
          <Outlet />
        </div>
      </main>

      {showAIWidget && (
        <aside className="ai-assistant-sidebar">
          <AIWidget />
        </aside>
      )}

      {/* Mobile overlay — closes sidebar on tap outside */}
      {sidebarOpen && (
        <div
          className="sidebar-overlay"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}

      <FloatingAskAIButton />
    </div>
  )
}
