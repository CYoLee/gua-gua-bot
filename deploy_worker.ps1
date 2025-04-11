# deploy_worker.ps1

$ErrorActionPreference = "Stop"

# === 設定參數 ===
$projectId = "gothic-depth-456114-c3"
$region = "asia-east1"
$serviceName = "gua-gua-bot-worker"
$image = "gcr.io/$projectId/$serviceName"

# === 設定 GCP 專案 ===
gcloud config set project $projectId

# === 建構與推送 Worker Image ===
Write-Host "`n=== Build & Push Worker Docker Image ==="
gcloud builds submit `
  --tag $image `
  --gcs-log-dir="gs://$projectId_cloudbuild/logs" `
  --gcs-source-staging-dir="gs://$projectId_cloudbuild/source" `
  --timeout=1200 `
  --config=worker/cloudbuild_worker.yaml

# === 部屬至 Cloud Run Worker ===
Write-Host "`n=== Deploy Worker to Cloud Run ==="
gcloud run deploy $serviceName `
  --image $image `
  --platform managed `
  --region $region `
  --allow-unauthenticated `
  --port 8080 `
  --memory 2Gi `
  --timeout 600 `
  --env-vars-file .env.yaml

Write-Host "`n✅ Worker Deployment completed successfully."
