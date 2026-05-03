param(
  [Parameter(Mandatory = $true)][string]$ProjectId,
  [string]$EnvFile = ".env"
)

if (-not (Test-Path $EnvFile)) {
  throw "Env file not found: $EnvFile"
}

gcloud config set project $ProjectId | Out-Null

$skip = @(
  "RUN_ML_TRAINING",
  "RUN_LLM_BRIEF",
  "RUN_TABLEAU_REFRESH",
  "RUN_VERTEX_PIPELINE",
  "VERTEX_PIPELINE_WAIT_FOR_COMPLETION",
  "TABLEAU_API_VERSION",
  "KAGGLE_DATASET",
  "GOOGLE_APPLICATION_CREDENTIALS"
)

Get-Content $EnvFile | ForEach-Object {
  $line = $_.Trim()
  if (-not $line -or $line.StartsWith("#")) { return }
  $parts = $line -split "=", 2
  if ($parts.Count -ne 2) { return }
  $name = $parts[0].Trim()
  $value = $parts[1].Trim()
  if (-not $name -or $skip -contains $name) { return }
  if (-not $value) { return }

  gcloud secrets describe $name 1>$null 2>$null
  if ($LASTEXITCODE -ne 0) {
    $value | gcloud secrets create $name --data-file=-
    Write-Host "Created secret: $name"
  } else {
    $value | gcloud secrets versions add $name --data-file=-
    Write-Host "Updated secret: $name"
  }
}

Write-Host "Secret sync complete."
