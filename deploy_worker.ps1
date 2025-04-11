# deploy_worker.ps1 - Background Worker for Redeem
$ErrorActionPreference = "Stop"

$projectId = "gothic-depth-456114-c3"
$region = "asia-east1"
$workerService = "gua-gua-bot-worker"
$image = "gcr.io/$projectId/$workerService"

gcloud config set project $projectId

Write-Host "`n=== Build & Push Worker Docker Image ==="
gcloud builds submit --tag $image --file Dockerfile.worker

Write-Host "`n=== Deploy Worker to Cloud Run ==="
gcloud run deploy $workerService `
  --image $image `
  --platform managed `
  --region $region `
  --port 8080 `
  --memory 2Gi `
  --timeout 600 `
  --no-allow-unauthenticated `
  --env-vars-file .env.yaml

Write-Host "`n✅ Worker Deployment complete!"
