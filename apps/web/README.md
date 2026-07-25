# AMC IQ Executive Demo UI

React + Vite + TypeScript single-page demo for the AMC IQ precision-oncology scenario.

## Run

```powershell
npm install
npm run dev
```

Vite serves on <http://localhost:5173> and proxies `/api` to the FastAPI backend at <http://localhost:8000>.

## Build

```powershell
npm run build
```

If the backend stream or non-streaming endpoint is unavailable, the UI renders `src/sampleResult.ts` so the hero scenario remains demoable.
