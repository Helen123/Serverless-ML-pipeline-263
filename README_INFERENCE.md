# Inference Setup & Usage Guide

This guide explains how to set up and use the online and batch inference capabilities of the ML pipeline.

## Quick Start

### 1. Run Setup Script

```bash
# Make sure you're in the project root
cd /Users/apple/Desktop/263

# Run the setup script
./scripts/setup_inference.sh
```

This script will:
- ✅ Create S3 prefixes (`to_infer/`, `predicted/`)
- ✅ Create IAM roles for both inference Lambdas
- ✅ Deploy OnlineInfer and BatchInfer Lambda functions
- ✅ Set up EventBridge rule for automatic batch inference
- ✅ Create and configure API Gateway for online inference

### 2. Test Inference

```bash
# Run the test script
./scripts/test_inference.sh
```

## Manual Setup (Alternative)

If you prefer to set up manually, follow the detailed guide:
- **Full Setup Guide**: `docs/inference_setup.md`

## Usage

### Online Inference (Real-time Predictions)

#### Via API Gateway (Recommended)

```bash
# Get your API endpoint URL (from setup script output or)
API_ID=$(aws apigateway get-rest-apis --region us-west-2 --query "items[?name=='MLPipelineInferenceAPI'].id" --output text)
API_URL="https://${API_ID}.execute-api.us-west-2.amazonaws.com/prod/predict"

# Make a prediction request
curl -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "area_sqm": 150.5,
    "latitude": 37.7749,
    "longitude": -122.4194,
    "house_age": 25,
    "med_inc": 4.5,
    "ave_rooms": 5.0,
    "ave_bedrms": 2.0,
    "population": 1000,
    "ave_occup": 3.0,
    "house_type": "house"
  }'
```

**Response:**
```json
{
  "predicted_price": 530000.00,
  "predicted_price_formatted": "$530,000.00",
  "features": {...}
}
```

#### Via Lambda (Direct)

```bash
cat > /tmp/payload.json <<EOF
{
  "area_sqm": 150.5,
  "latitude": 37.7749,
  "longitude": -122.4194,
  "house_age": 25,
  "med_inc": 4.5,
  "ave_rooms": 5.0,
  "ave_bedrms": 2.0,
  "population": 1000,
  "ave_occup": 3.0,
  "house_type": "house"
}
EOF

aws lambda invoke \
  --function-name OnlineInfer \
  --cli-binary-format raw-in-base64-out \
  --payload file:///tmp/payload.json \
  /tmp/response.json

cat /tmp/response.json | python3 -m json.tool
```

### Batch Inference (Bulk Predictions)

#### Automatic Trigger (Recommended)

Simply upload a CSV file to the `to_infer/` prefix in S3:

```bash
# Upload your data file
aws s3 cp your_data.csv s3://ml-pipeline-dev-954976298878-us-west-2-helen/to_infer/batch_20251108.csv
```

The system will automatically:
1. Detect the upload via EventBridge
2. Trigger BatchInfer Lambda
3. Start SageMaker Batch Transform job
4. Save predictions to `predicted/` prefix

#### Monitor Batch Jobs

```bash
# List batch transform jobs
aws sagemaker list-transform-jobs \
  --name-contains batch-infer \
  --region us-west-2

# Check job status
aws sagemaker describe-transform-job \
  --transform-job-name batch-infer-20251108-123456 \
  --region us-west-2

# Check output files
aws s3 ls s3://ml-pipeline-dev-954976298878-us-west-2-helen/predicted/ --recursive
```

#### Manual Trigger

```bash
cat > /tmp/batch-payload.json <<EOF
{
  "bucket": "ml-pipeline-dev-954976298878-us-west-2-helen",
  "input_key": "to_infer/batch_20251108.csv",
  "model_name": "house-price-model-20251108-032257"
}
EOF

aws lambda invoke \
  --function-name BatchInfer \
  --cli-binary-format raw-in-base64-out \
  --payload file:///tmp/batch-payload.json \
  /tmp/batch-response.json
```

## Input Format

### Online Inference Input

The online inference expects a JSON object with the following fields:

```json
{
  "area_sqm": 150.5,        // Required: Area in square meters
  "latitude": 37.7749,      // Required: Latitude
  "longitude": -122.4194,   // Required: Longitude
  "house_age": 25,          // Required: Age of house in years
  "med_inc": 4.5,           // Required: Median income
  "ave_rooms": 5.0,         // Required: Average rooms
  "ave_bedrms": 2.0,        // Required: Average bedrooms
  "population": 1000,       // Required: Population
  "ave_occup": 3.0,         // Required: Average occupancy
  "house_type": "house"     // Optional: Type of house (house, apartment, townhouse)
}
```

### Batch Inference Input

The batch inference expects a CSV file with the same columns (without `house_type` if not available):

```csv
area_sqm,latitude,longitude,house_age,med_inc,ave_rooms,ave_bedrms,population,ave_occup,house_type
150.5,37.7749,-122.4194,25,4.5,5.0,2.0,1000,3.0,house
200.0,37.7849,-122.4094,30,5.0,6.0,3.0,1500,3.5,apartment
```

**Note:** The CSV should contain **raw features** (not feature-engineered). The Lambda will apply the same feature engineering as used in training.

## Architecture

### Online Inference Flow

```
User/Client
    ↓
API Gateway (REST API)
    ↓
OnlineInfer Lambda
    ↓ (applies feature engineering)
SageMaker Endpoint (Serverless)
    ↓
Prediction Response
    ↓
API Gateway → User
```

### Batch Inference Flow

```
S3 Upload (to_infer/)
    ↓
EventBridge (S3 Object Created Event)
    ↓
BatchInfer Lambda
    ↓
SageMaker Batch Transform Job
    ↓
Predictions → S3 (predicted/)
```

## Troubleshooting

### Online Inference Issues

**"Endpoint not found"**
```bash
# Check endpoint status
aws sagemaker describe-endpoint --endpoint-name house-price-endpoint --region us-west-2
```

**"Invalid feature format"**
- Ensure all required fields are provided
- Check that numeric values are valid numbers
- Verify feature engineering matches training data

**CORS errors**
- The Lambda already includes CORS headers
- If using a web frontend, ensure API Gateway CORS is configured

### Batch Inference Issues

**"Model not found"**
- Provide explicit `model_name` in the event payload
- Or ensure model is registered in DynamoDB with `is_deployed=true`

**"Transform job failed"**
```bash
# Check CloudWatch logs
aws logs tail /aws/lambda/BatchInfer --follow --region us-west-2

# Check transform job logs
aws sagemaker describe-transform-job \
  --transform-job-name <job-name> \
  --region us-west-2
```

**"EventBridge not triggering"**
```bash
# Check EventBridge rule status
aws events describe-rule --name BatchInferTrigger --region us-west-2

# Check Lambda permissions
aws lambda get-policy --function-name BatchInfer --region us-west-2
```

## Cost Optimization

- **Serverless Inference**: Only pay for actual inference requests (no idle costs)
- **Batch Transform**: More cost-effective for large batches than endpoint-based inference
- **API Gateway**: Pay per API call (first 1M requests/month free)

## Next Steps

1. **Add input validation** for better error handling
2. **Implement caching** for frequently requested predictions
3. **Set up CloudWatch alarms** for monitoring
4. **Create a web frontend** for user-friendly predictions
5. **Add rate limiting** in API Gateway

## Related Files

- **Setup Script**: `scripts/setup_inference.sh`
- **Test Script**: `scripts/test_inference.sh`
- **Detailed Setup Guide**: `docs/inference_setup.md`
- **Online Inference Lambda**: `lambdas/online_infer/handler.py`
- **Batch Inference Lambda**: `lambdas/batch_infer/handler.py`

