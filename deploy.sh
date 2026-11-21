#!/bin/bash

# Leslie Agency OS - Production Deployment Script
# Usage: ./deploy.sh [PROJECT_ID] [REGION]

PROJECT_ID=${1:-$(gcloud config get-value project)}
REGION=${2:-"europe-west1"}
SERVICE_NAME="leslie-agency-os"
IMAGE_NAME="gcr.io/$PROJECT_ID/$SERVICE_NAME"

echo "🔱 Leslie Production Deployment"
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "-----------------------------------"

if [ -z "$PROJECT_ID" ]; then
    echo "❌ Error: PROJECT_ID not found. Please provide it as first argument."
    exit 1
fi

# 1. Build & Push to GCR
echo "[1/2] Building and pushing container image..."
gcloud builds submit --tag $IMAGE_NAME .

# 2. Deploy to Cloud Run
echo "[2/2] Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
    --image $IMAGE_NAME \
    --platform managed \
    --region $REGION \
    --allow-unauthenticated \
    --memory 1Gi \
    --cpu 1 \
    --timeout 3600 \
    --min-instances 0 \
    --max-instances 1 \
    --set-env-vars FLASK_ENV=production,GCS_BUCKET=agency-os-data-664856461640 \
    --update-secrets=GOOGLE_MAPS_API_KEY=GOOGLE_MAPS_API_KEY:latest \
    --update-secrets=DEEPSEEK_API_KEY=DEEPSEEK_API_KEY:latest \
    --update-secrets=SECRET_KEY=LESLIE_SECRET_KEY:latest

echo "-----------------------------------"
echo "🚀 Leslie is LIVE!"
gcloud run services describe $SERVICE_NAME --region $REGION --format='value(status.url)'
