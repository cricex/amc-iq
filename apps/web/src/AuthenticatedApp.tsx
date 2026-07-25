import { lazy, Suspense } from 'react'

import App from './App'

const AuthenticationProvider = lazy(async () => {
  const module = await import('./auth')
  return { default: module.AuthenticationProvider }
})

export default function AuthenticatedApp() {
  return (
    <Suspense fallback={null}>
      <AuthenticationProvider>
        <App />
      </AuthenticationProvider>
    </Suspense>
  )
}
