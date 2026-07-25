import {
  InteractionRequiredAuthError,
  PublicClientApplication,
  type AccountInfo,
  type AuthenticationResult,
} from '@azure/msal-browser'
import { MsalProvider, useMsal } from '@azure/msal-react'
import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { AuthenticationContext, AuthenticationError } from './authentication'

const tenantId = (import.meta.env.VITE_ENTRA_TENANT_ID ?? '').trim()
const clientId = (import.meta.env.VITE_ENTRA_CLIENT_ID ?? '').trim()
const apiScope = (import.meta.env.VITE_API_SCOPE ?? '').trim()
const redirectUri = (import.meta.env.VITE_REDIRECT_URI ?? window.location.origin).trim()

function configured(): boolean {
  return Boolean(tenantId && clientId && apiScope)
}

function MsalAuthenticationBridge({ children }: { children: ReactNode }) {
  const { instance, accounts } = useMsal()

  const getAccessToken = useCallback(async (): Promise<string> => {
    try {
      await instance.initialize()
      let account: AccountInfo | null = instance.getActiveAccount() ?? accounts[0] ?? null
      if (!account) {
        const login: AuthenticationResult = await instance.loginPopup({
          scopes: [apiScope],
          redirectUri,
          prompt: 'select_account',
        })
        account = login.account
        instance.setActiveAccount(account)
      }

      try {
        const result = await instance.acquireTokenSilent({
          account,
          scopes: [apiScope],
          redirectUri,
        })
        return result.accessToken
      } catch (error) {
        if (!(error instanceof InteractionRequiredAuthError)) throw error
        const result = await instance.acquireTokenPopup({
          account,
          scopes: [apiScope],
          redirectUri,
        })
        return result.accessToken
      }
    } catch (error) {
      if (error instanceof AuthenticationError) throw error
      throw new AuthenticationError('Microsoft Entra sign-in or token acquisition failed.')
    }
  }, [accounts, instance])

  const value = useMemo(() => ({ getAccessToken }), [getAccessToken])
  return <AuthenticationContext.Provider value={value}>{children}</AuthenticationContext.Provider>
}

function ConfiguredAuthenticationProvider({ children }: { children: ReactNode }) {
  const [instance] = useState(
    () =>
      new PublicClientApplication({
        auth: {
          clientId,
          authority: `https://login.microsoftonline.com/${tenantId}`,
          redirectUri,
        },
        cache: {
          cacheLocation: 'sessionStorage',
        },
      }),
  )

  return (
    <MsalProvider instance={instance}>
      <MsalAuthenticationBridge>{children}</MsalAuthenticationBridge>
    </MsalProvider>
  )
}

export function AuthenticationProvider({ children }: { children: ReactNode }) {
  if (!configured()) {
    return (
      <AuthenticationContext.Provider
        value={{
          getAccessToken: async () => null,
        }}
      >
        {children}
      </AuthenticationContext.Provider>
    )
  }

  return <ConfiguredAuthenticationProvider>{children}</ConfiguredAuthenticationProvider>
}
