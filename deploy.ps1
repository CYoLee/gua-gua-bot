# deploy.ps1

$ErrorActionPreference = "Stop"

$projectId = "gothic-depth-456114-c3"
$region = "asia-east1"
$serviceName = "gua-gua-bot"

Write-Host "`n=== Git Status Check ==="
$changedFiles = git status --porcelain

if (-not $changedFiles) {
    Write-Host "No changes to commit. Skipping Git step."
} else {
    $fileList = ($changedFiles | ForEach-Object { $_.Substring(3).Trim() }) -join ' '
    $commitMsg = "auto: update $fileList"

    Write-Host "`n=== Committing and Pushing ==="
    git add .
    git commit -m "$commitMsg"
    git push origin main
}

Write-Host "`n=== Setting GCP Project [$projectId] ==="
gcloud config set project $projectId

Write-Host "`n=== Building Docker Image ==="
gcloud builds submit --tag "gcr.io/$projectId/$serviceName"

Write-Host "`n=== Fetching Latest Image SHA ==="
$imageSha = gcloud artifacts docker images list gcr.io/$projectId/$serviceName `
  --format="value(digest)" | Select-Object -First 1

$image = "gcr.io/$projectId/$serviceName@$imageSha"

Write-Host "`n=== Deploying to Cloud Run [$image] ==="
gcloud run deploy $serviceName `
  --image $image `
  --platform managed `
  --region $region `
  --allow-unauthenticated `
  --port 8080 `
  --env-vars-file .env.yaml

Write-Host "`n Deployment completed successfully."
