import { createContext, useContext } from 'react'

export class AuthenticationError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'AuthenticationError'
  }
}

export interface AuthenticationContextValue {
  getAccessToken: () => Promise<string | null>
}

export const AuthenticationContext = createContext<AuthenticationContextValue>({
  getAccessToken: async () => {
    throw new AuthenticationError('Microsoft Entra authentication is not configured.')
  },
})

export function useAuthentication(): AuthenticationContextValue {
  return useContext(AuthenticationContext)
}
