# Infra: GCP Setup

## First-time setup

```bash
# 1. Authenticate with gcloud (one-time)
gcloud auth login

# 2. Run the setup script
chmod +x infra/setup.sh
./infra/setup.sh YOUR_GCP_PROJECT_ID

# Optional: pass a region (default: us-central1)
./infra/setup.sh YOUR_GCP_PROJECT_ID europe-west1
```

The script prints the two values to add to `backend/.env` when it finishes.

---

## What the script provisions

| Resource | Name | Notes |
|----------|------|-------|
| GCP APIs | firestore, storage, aiplatform, texttospeech, run, firebase, cloudbuild, logging | Enabled on the project |
| Firestore | `(default)` database, Native mode, `us-central1` | One per project |
| GCS bucket | `{project-id}-story-assets` | Uniform bucket-level access |
| Service account | `voice-story-agent-sa@{project}.iam.gserviceaccount.com` | — |
| IAM roles | `datastore.user`, `storage.objectAdmin`, `aiplatform.user`, `logging.logWriter`, `cloudtexttospeech.serviceAgent` | Bound to the SA |
| SA key file | `.credentials/sa-key.json` | Git-ignored; local dev only |

---

## Re-running on an already-configured project

The script is safe to re-run. Existing resources produce a `[WARN]` message and are
skipped — the script does not exit with an error.

Known cases that may need manual intervention:

| Situation | What happens | Manual fix |
|-----------|-------------|------------|
| Firestore database exists but is in **Datastore mode** | Script skips creation with a warning | In Cloud Console: Firestore → cannot be converted. You must use a different project or delete all data and recreate. |
| GCS bucket exists in a **different region** | Script skips creation with a warning | Delete the bucket manually (`gcloud storage rm -r gs://...`) and re-run, or update `GCS_BUCKET_NAME` to point to the existing bucket. |
| SA key file already exists at `.credentials/sa-key.json` | Script skips key download | Delete the file manually and re-run if you need a fresh key. |
| Insufficient IAM permissions to bind roles | Role binding step warns and continues | Ask a project Owner to run `gcloud projects add-iam-policy-binding` for the missing roles. |

---

## Credentials for local development

**Recommended (no key file):**
```bash
gcloud auth application-default login
# Then set GCP_PROJECT_ID in backend/.env — that's all.
```

**Alternative (service account key):**
```bash
export GOOGLE_APPLICATION_CREDENTIALS="$(pwd)/.credentials/sa-key.json"
# Add to your shell profile to persist across sessions.
```

The `.credentials/` directory is git-ignored. Never commit `sa-key.json`.

---

## Credentials for Cloud Run (production)

Cloud Run uses the service account identity directly — no key file is needed.
Attach the SA to the Cloud Run service at deploy time:

```bash
gcloud run deploy voice-story-agent-backend \
  --service-account=voice-story-agent-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com \
  ...
```

See `quickstart.md` §6a for the full deploy command.
