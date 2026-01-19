const express = require("express")
const { createClient } = require("@supabase/supabase-js")
const { spawn } = require("child_process")
const fs = require("fs/promises")
const path = require("path")
const os = require("os")
const crypto = require("crypto")

const PORT = process.env.PORT || 8080
const RUN_SECRET = process.env.ASR_WORKER_SECRET || ""
const RUN_SECRET_HEADER = process.env.ASR_WORKER_HEADER || "x-run-secret"

const SUPABASE_URL = process.env.SUPABASE_URL || ""
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || ""
const SUPABASE_STORAGE_BUCKET = process.env.SUPABASE_STORAGE_BUCKET || ""

const ASSEMBLYAI_API_KEY = process.env.ASSEMBLYAI_API_KEY || ""
const ASR_WEBHOOK_URL = process.env.ASR_WEBHOOK_URL || ""
const ASR_WEBHOOK_SECRET = process.env.ASR_WEBHOOK_SECRET || ""
const ASR_WEBHOOK_HEADER = process.env.ASR_WEBHOOK_HEADER || "x-asr-webhook-secret"

const parsedTtl = Number.parseInt(process.env.SIGNED_URL_TTL_SECONDS || "86400", 10)
const SIGNED_URL_TTL_SECONDS = Number.isFinite(parsedTtl) ? parsedTtl : 86400

function requireEnv(name, value) {
  if (!value) {
    throw new Error(`${name} not configured`)
  }
}

function sanitizeId(value) {
  return String(value || "").replace(/[^a-zA-Z0-9_-]/g, "") || "unknown"
}

function runCommand(cmd, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, options)
    let stderr = ""
    child.stderr.on("data", (data) => {
      stderr += data.toString()
    })
    child.on("error", reject)
    child.on("close", (code) => {
      if (code === 0) {
        resolve()
      } else {
        reject(new Error(`${cmd} exited with code ${code}: ${stderr.trim()}`))
      }
    })
  })
}

async function downloadAudio(youtubeUrl, videoId) {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "asr-"))
  const safeVideoId = sanitizeId(videoId)
  const outputTemplate = path.join(tempDir, `${safeVideoId}.%(ext)s`)

  const args = [
    "--no-playlist",
    "-f", "bestaudio",
    "-x",
    "--audio-format", "mp3",
    "--audio-quality", "0",
    "--no-progress",
    "-o", outputTemplate,
    youtubeUrl,
  ]

  await runCommand("yt-dlp", args)

  const files = await fs.readdir(tempDir)
  const audioFile = files.find((file) => file.endsWith(".mp3")) || files[0]
  if (!audioFile) {
    throw new Error("yt-dlp did not produce an audio file")
  }

  return {
    tempDir,
    filePath: path.join(tempDir, audioFile),
  }
}

async function uploadAudio(supabase, filePath, videoId) {
  const buffer = await fs.readFile(filePath)
  const safeVideoId = sanitizeId(videoId)
  const objectPath = `asr/${safeVideoId}/${crypto.randomUUID()}.mp3`

  const { error: uploadError } = await supabase.storage
    .from(SUPABASE_STORAGE_BUCKET)
    .upload(objectPath, buffer, {
      contentType: "audio/mpeg",
      upsert: true,
    })

  if (uploadError) {
    throw new Error(`Supabase upload failed: ${uploadError.message}`)
  }

  const { data: signedData, error: signedError } = await supabase.storage
    .from(SUPABASE_STORAGE_BUCKET)
    .createSignedUrl(objectPath, SIGNED_URL_TTL_SECONDS)

  if (signedError || !signedData?.signedUrl) {
    throw new Error(`Signed URL failed: ${signedError?.message || "missing signedUrl"}`)
  }

  return {
    audio_url: signedData.signedUrl,
    audio_path: objectPath,
    audio_bucket: SUPABASE_STORAGE_BUCKET,
  }
}

async function submitToAssemblyAI(audioUrl) {
  const payload = {
    audio_url: audioUrl,
    language_detection: true,
    webhook_url: ASR_WEBHOOK_URL,
  }

  if (ASR_WEBHOOK_SECRET) {
    payload.webhook_auth_header_name = ASR_WEBHOOK_HEADER
    payload.webhook_auth_header_value = ASR_WEBHOOK_SECRET
  }

  const response = await fetch("https://api.assemblyai.com/v2/transcript", {
    method: "POST",
    headers: {
      authorization: ASSEMBLYAI_API_KEY,
      "content-type": "application/json",
    },
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    const text = await response.text()
    throw new Error(`AssemblyAI API error (${response.status}): ${text}`)
  }

  const data = await response.json()
  if (!data?.id) {
    throw new Error("AssemblyAI did not return transcript id")
  }

  return data.id
}

async function cleanupStorage(supabase, audioInfo) {
  if (!audioInfo?.audio_bucket || !audioInfo?.audio_path) return
  await supabase.storage
    .from(audioInfo.audio_bucket)
    .remove([audioInfo.audio_path])
}

const app = express()
app.use(express.json({ limit: "1mb" }))

app.get("/health", (_req, res) => {
  res.json({ status: "ok" })
})

app.post("/process-asr", async (req, res) => {
  try {
    if (RUN_SECRET) {
      const provided = req.header(RUN_SECRET_HEADER)
      if (provided !== RUN_SECRET) {
        return res.status(401).json({ error: "Unauthorized" })
      }
    }

    requireEnv("SUPABASE_URL", SUPABASE_URL)
    requireEnv("SUPABASE_SERVICE_ROLE_KEY", SUPABASE_SERVICE_ROLE_KEY)
    requireEnv("SUPABASE_STORAGE_BUCKET", SUPABASE_STORAGE_BUCKET)
    requireEnv("ASSEMBLYAI_API_KEY", ASSEMBLYAI_API_KEY)
    requireEnv("ASR_WEBHOOK_URL", ASR_WEBHOOK_URL)

    const { youtube_url: youtubeUrl, video_id: videoId } = req.body || {}
    if (!youtubeUrl || !videoId) {
      return res.status(400).json({ error: "Missing youtube_url or video_id" })
    }

    const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
      auth: { persistSession: false },
    })

    let tempDir = null
    let audioInfo = null

    try {
      const download = await downloadAudio(youtubeUrl, videoId)
      tempDir = download.tempDir
      audioInfo = await uploadAudio(supabase, download.filePath, videoId)

      const externalId = await submitToAssemblyAI(audioInfo.audio_url)

      return res.json({
        external_id: externalId,
        audio_url: audioInfo.audio_url,
        audio_path: audioInfo.audio_path,
        audio_bucket: audioInfo.audio_bucket,
      })
    } catch (error) {
      if (audioInfo) {
        try {
          await cleanupStorage(supabase, audioInfo)
        } catch (cleanupError) {
          console.warn("Failed to cleanup audio after error", cleanupError)
        }
      }
      throw error
    } finally {
      if (tempDir) {
        await fs.rm(tempDir, { recursive: true, force: true })
      }
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    console.error("ASR worker failed:", message)
    return res.status(500).json({ error: message })
  }
})

app.listen(PORT, () => {
  console.log(`ASR worker listening on ${PORT}`)
})
