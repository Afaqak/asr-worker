# Cloud Run ASR Worker Deployment

This service runs `yt-dlp` + `ffmpeg`, uploads audio to Supabase Storage, and submits the audio to AssemblyAI.

## Service Location
`services/asr-worker`

## Required Env Vars
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_STORAGE_BUCKET` (e.g. `asr-audio`)
- `ASSEMBLYAI_API_KEY`
- `ASR_WEBHOOK_URL`
- `ASR_WEBHOOK_SECRET`
- `ASR_WEBHOOK_HEADER` (optional, default `x-asr-webhook-secret`)
- `ASR_WORKER_SECRET` (shared with `ASR_WORKER_SECRET` in the Edge function)
- `ASR_WORKER_HEADER` (optional, default `x-run-secret`)

## Build & Deploy
```bash
gcloud builds submit services/asr-worker \
  --tag gcr.io/YOUR_PROJECT/asr-worker

gcloud run deploy asr-worker \
  --image gcr.io/YOUR_PROJECT/asr-worker \
  --region YOUR_REGION \
  --platform managed \
  --allow-unauthenticated \
  --concurrency 1 \
  --max-instances 3 \
  --set-env-vars SUPABASE_URL=...,SUPABASE_SERVICE_ROLE_KEY=...,SUPABASE_STORAGE_BUCKET=asr-audio,ASSEMBLYAI_API_KEY=...,ASR_WEBHOOK_URL=...,ASR_WEBHOOK_SECRET=...,ASR_WEBHOOK_HEADER=x-asr-webhook-secret,ASR_WORKER_SECRET=...,ASR_WORKER_HEADER=x-run-secret
```

## Verify
```bash
curl -X POST https://YOUR_CLOUD_RUN_URL/process-asr \
  -H "Content-Type: application/json" \
  -H "x-run-secret: YOUR_SECRET" \
  -d '{"youtube_url":"https://www.youtube.com/watch?v=VIDEO_ID","video_id":"VIDEO_ID"}'
```
