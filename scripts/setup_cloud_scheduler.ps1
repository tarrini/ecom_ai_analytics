param(
  [Parameter(Mandatory = $true)][string]$ProjectId,
  [string]$Region = "us-central1",
  [string]$Image = "",
  [string]$JobName = "ecom-pipeline-job",
  [string]$SchedulerName = "ecom-pipeline-daily",
  [string]$Schedule = "0 8 * * *",
  [string]$TimeZone = "Asia/Kolkata"
)

if (-not $Image) {
  $Image = "gcr.io/$ProjectId/ecom-ai-analytics:latest"
}

$RunnerSa = "ecom-runner@$ProjectId.iam.gserviceaccount.com"
$SchedulerSa = "ecom-scheduler@$ProjectId.iam.gserviceaccount.com"

Write-Host "Configuring project..."
gcloud config set project $ProjectId
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com iam.googleapis.com

Write-Host "Creating service accounts (idempotent)..."
gcloud iam service-accounts create ecom-runner --display-name "Ecom Pipeline Runner" 2>$null
gcloud iam service-accounts create ecom-scheduler --display-name "Ecom Scheduler Invoker" 2>$null

Write-Host "Granting IAM roles..."
gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$RunnerSa" --role "roles/storage.admin"
gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$RunnerSa" --role "roles/aiplatform.user"
gcloud projects add-iam-policy-binding $ProjectId --member "serviceAccount:$RunnerSa" --role "roles/secretmanager.secretAccessor"

Write-Host "Building image..."
gcloud builds submit --tag $Image

Write-Host "Creating/Updating Cloud Run job..."
gcloud run jobs describe $JobName --region $Region 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
  gcloud run jobs create $JobName `
    --image $Image `
    --region $Region `
    --service-account $RunnerSa `
    --set-env-vars "RUN_ML_TRAINING=true,RUN_LLM_BRIEF=true,RUN_TABLEAU_REFRESH=true,RUN_VERTEX_PIPELINE=false,TABLEAU_API_VERSION=3.22,KAGGLE_DATASET=olistbr/brazilian-ecommerce" `
    --command python `
    --args pipeline/run_full_pipeline.py
} else {
  gcloud run jobs update $JobName `
    --image $Image `
    --region $Region `
    --service-account $RunnerSa
}

Write-Host "Allowing scheduler to invoke run job..."
gcloud run jobs add-iam-policy-binding $JobName `
  --region $Region `
  --member "serviceAccount:$SchedulerSa" `
  --role "roles/run.invoker"

Write-Host "Creating/Updating scheduler..."
gcloud scheduler jobs describe $SchedulerName --location $Region 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
  gcloud scheduler jobs create http $SchedulerName `
    --location $Region `
    --schedule "$Schedule" `
    --time-zone "$TimeZone" `
    --uri "https://$Region-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$ProjectId/jobs/$JobName:run" `
    --http-method POST `
    --oauth-service-account-email $SchedulerSa `
    --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform"
} else {
  gcloud scheduler jobs update http $SchedulerName `
    --location $Region `
    --schedule "$Schedule" `
    --time-zone "$TimeZone" `
    --uri "https://$Region-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$ProjectId/jobs/$JobName:run" `
    --http-method POST `
    --oauth-service-account-email $SchedulerSa `
    --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform"
}

Write-Host "Done. Trigger once with:"
Write-Host "gcloud scheduler jobs run $SchedulerName --location $Region"
