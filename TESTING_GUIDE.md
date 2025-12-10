# Testing Guide: Online & Batch Inference

This guide shows you how to test both online and batch inference functionality.

## Prerequisites

1. **Ensure setup is complete**: Run `./scripts/setup_inference.sh` first if you haven't already
2. **Verify endpoint exists**: The SageMaker endpoint should be deployed and in "InService" status
3. **Set environment variables**:
   ```bash
   export AWS_PROFILE=helen1
   export AWS_REGION=us-west-2
   export BUCKET=ml-pipeline-dev-954976298878-us-west-2-helen
   export ENDPOINT_NAME=house-price-endpoint
   ```

---

## 🚀 Quick Test (Automated)

Run the automated test script:

```bash
./scripts/test_inference.sh
```

This will test:
- ✅ Online inference via Lambda (direct)
- ✅ Online inference via API Gateway
- ✅ Batch inference (S3 upload trigger)
- ✅ Endpoint status check

---

## 📝 Manual Testing Steps

### Test 1: Online Inference via Lambda (Direct)

**What this tests**: The `OnlineInfer` Lambda function directly, bypassing API Gateway.

```bash
# Create test payload
cat > /tmp/test-payload.json <<EOF
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

# Invoke Lambda
aws lambda invoke \
  --function-name OnlineInfer \
  --cli-binary-format raw-in-base64-out \
  --payload file:///tmp/test-payload.json \
  /tmp/lambda-response.json \
  --region $AWS_REGION

# View response
cat /tmp/lambda-response.json | python3 -m json.tool
```

**Expected Response**:
```json
{
  "statusCode": 200,
  "predicted_price": 530000.00,
  "predicted_price_formatted": "$530,000.00"
}
```

**What to verify**:
- ✅ Lambda executes without errors
- ✅ Response contains `predicted_price` field
- ✅ Price is a reasonable number (not negative or extremely large)

---

### Test 2: Online Inference via API Gateway

**What this tests**: The full API Gateway → Lambda → SageMaker endpoint flow.

#### Step 1: Get API Gateway URL

```bash
# Get API ID
API_ID=$(aws apigateway get-rest-apis \
  --region $AWS_REGION \
  --query "items[?name=='MLPipelineInferenceAPI'].id" \
  --output text)

# Construct URL
API_URL="https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/prod/predict"
echo "API URL: $API_URL"
```

#### Step 2: Send POST Request

```bash
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
  }' \
  -w "\nHTTP Status: %{http_code}\n"
```

**Expected Response**:
```json
{
  "statusCode": 200,
  "predicted_price": 530000.00,
  "predicted_price_formatted": "$530,000.00"
}
HTTP Status: 200
```

**What to verify**:
- ✅ HTTP status code is 200
- ✅ Response contains predicted price
- ✅ Response time is reasonable (< 5 seconds for cold start)

#### Step 3: Test with Different Inputs

```bash
# Test apartment
curl -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "area_sqm": 80.0,
    "latitude": 37.7849,
    "longitude": -122.4094,
    "house_age": 10,
    "med_inc": 3.0,
    "ave_rooms": 3.0,
    "ave_bedrms": 1.5,
    "population": 500,
    "ave_occup": 2.0,
    "house_type": "apartment"
  }'

# Test townhouse
curl -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "area_sqm": 120.0,
    "latitude": 37.7649,
    "longitude": -122.4294,
    "house_age": 15,
    "med_inc": 4.0,
    "ave_rooms": 4.5,
    "ave_bedrms": 2.0,
    "population": 800,
    "ave_occup": 2.5,
    "house_type": "townhouse"
  }'
```

**What to verify**:
- ✅ Different house types return different prices
- ✅ Prices are reasonable relative to input features

---

### Test 3: Batch Inference

**What this tests**: EventBridge trigger → BatchInfer Lambda → SageMaker Batch Transform → S3 output.

#### Step 1: Create Test CSV File

```bash
cat > /tmp/batch_test.csv <<EOF
area_sqm,latitude,longitude,house_age,med_inc,ave_rooms,ave_bedrms,population,ave_occup,house_type
150.5,37.7749,-122.4194,25,4.5,5.0,2.0,1000,3.0,house
200.0,37.7849,-122.4094,30,5.0,6.0,3.0,1500,3.5,apartment
120.0,37.7649,-122.4294,20,3.5,4.0,2.0,800,2.5,townhouse
180.0,37.7949,-122.3994,35,6.0,7.0,3.0,2000,4.0,house
100.0,37.7549,-122.4394,15,3.0,3.5,1.5,600,2.0,apartment
EOF
```

#### Step 2: Upload to S3 (This Triggers Batch Inference)

```bash
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BATCH_FILE="batch_test_${TIMESTAMP}.csv"

aws s3 cp /tmp/batch_test.csv \
  "s3://$BUCKET/to_infer/$BATCH_FILE" \
  --region $AWS_REGION

echo "✓ Uploaded: s3://$BUCKET/to_infer/$BATCH_FILE"
```

**What happens**:
1. EventBridge detects the S3 upload
2. EventBridge triggers `BatchInfer` Lambda
3. Lambda creates a SageMaker Batch Transform job
4. Batch Transform processes the CSV
5. Predictions are written to `s3://$BUCKET/predicted/`

#### Step 3: Monitor Batch Transform Job

```bash
# List recent batch transform jobs
aws sagemaker list-transform-jobs \
  --name-contains batch-infer \
  --region $AWS_REGION \
  --max-results 5 \
  --query 'TransformJobSummaries[*].{Name:TransformJobName,Status:TransformJobStatus,CreationTime:CreationTime}' \
  --output table
```

**Check job status**:
```bash
# Get the latest job name
JOB_NAME=$(aws sagemaker list-transform-jobs \
  --name-contains batch-infer \
  --region $AWS_REGION \
  --max-results 1 \
  --query 'TransformJobSummaries[0].TransformJobName' \
  --output text)

# Describe the job
aws sagemaker describe-transform-job \
  --transform-job-name $JOB_NAME \
  --region $AWS_REGION \
  --query '{Status:TransformJobStatus,OutputPath:TransformOutput.S3OutputPath}' \
  --output json
```

**Status values**:
- `InProgress`: Job is running
- `Completed`: Job finished successfully
- `Failed`: Job encountered an error

#### Step 4: Check Lambda Invocation (Optional)

```bash
# Check if BatchInfer Lambda was triggered
aws logs tail /aws/lambda/BatchInfer \
  --since 10m \
  --region $AWS_REGION \
  --format short
```

#### Step 5: Retrieve Predictions from S3

Wait for the job to complete (usually 2-5 minutes), then:

```bash
# List prediction files
aws s3 ls s3://$BUCKET/predicted/ --recursive

# Download the latest prediction file
LATEST_PRED=$(aws s3 ls s3://$BUCKET/predicted/ --recursive | tail -1 | awk '{print $4}')
aws s3 cp "s3://$BUCKET/$LATEST_PRED" /tmp/predictions.csv

# View predictions
cat /tmp/predictions.csv
```

**Expected Output** (CSV with predictions):
```
0.530000
0.650000
0.420000
0.720000
0.380000
```

Each line is a predicted price (normalized) for one row in the input CSV.

**What to verify**:
- ✅ Batch transform job completes successfully
- ✅ Predictions file exists in `predicted/` prefix
- ✅ Number of predictions matches number of input rows
- ✅ Predictions are reasonable values

---

## 🔍 Troubleshooting

### Online Inference Issues

**Problem**: "Endpoint not found" or "ResourceNotFoundException"
```bash
# Check endpoint status
aws sagemaker describe-endpoint \
  --endpoint-name $ENDPOINT_NAME \
  --region $AWS_REGION \
  --query 'EndpointStatus'
```
- If endpoint doesn't exist, deploy it first using `DeployModel` Lambda
- If status is not "InService", wait for deployment to complete

**Problem**: "Invalid feature format" or feature mismatch
- Ensure all required fields are provided
- Check that feature engineering in `OnlineInfer` Lambda matches training
- Verify numeric values are valid numbers

**Problem**: API Gateway returns 502/500
```bash
# Check Lambda logs
aws logs tail /aws/lambda/OnlineInfer \
  --since 10m \
  --region $AWS_REGION \
  --format short
```

### Batch Inference Issues

**Problem**: Batch inference not triggered
```bash
# Check EventBridge rule
aws events list-rules \
  --name-prefix batch-infer \
  --region $AWS_REGION

# Check Lambda logs
aws logs tail /aws/lambda/BatchInfer \
  --since 10m \
  --region $AWS_REGION \
  --format short
```

**Problem**: Batch transform job fails
```bash
# Get job details
aws sagemaker describe-transform-job \
  --transform-job-name <job-name> \
  --region $AWS_REGION

# Check CloudWatch logs for the transform job
aws logs tail /aws/sagemaker/TransformJobs \
  --since 30m \
  --region $AWS_REGION
```

**Problem**: No predictions in S3
- Wait 5-10 minutes for job to complete
- Check job status (should be "Completed")
- Verify S3 output path in job configuration

---

## ✅ Verification Checklist

After testing, verify:

- [ ] Online inference via Lambda returns valid predictions
- [ ] Online inference via API Gateway returns valid predictions
- [ ] API Gateway response time is acceptable (< 5s)
- [ ] Batch inference is triggered automatically on S3 upload
- [ ] Batch transform job completes successfully
- [ ] Predictions are written to `predicted/` prefix
- [ ] Number of predictions matches input rows
- [ ] Prediction values are reasonable

---

## 📊 Expected Results Summary

| Test | Expected Outcome |
|------|------------------|
| Online Inference (Lambda) | Returns JSON with `predicted_price` |
| Online Inference (API Gateway) | HTTP 200, JSON response with price |
| Batch Inference | CSV file in `predicted/` with one price per row |
| Endpoint Status | "InService" |

---

## 🎯 Next Steps

Once testing is successful:
1. Integrate API Gateway URL into your application
2. Set up monitoring/alerting for inference latency
3. Test with larger batch files (100+ rows)
4. Consider adding input validation and error handling

