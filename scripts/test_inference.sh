#!/bin/bash
# Test Script for Online & Batch Inference

set -e

echo "=========================================="
echo "Testing Inference Functions"
echo "=========================================="

# Set environment variables
export AWS_PROFILE=${AWS_PROFILE:-helen1}
export AWS_REGION=${AWS_REGION:-us-west-2}
export BUCKET=${BUCKET:-ml-pipeline-dev-954976298878-us-west-2-helen}
export ENDPOINT_NAME=${ENDPOINT_NAME:-house-price-endpoint}
export ONLINE_INFER_FUNCTION_NAME=${ONLINE_INFER_FUNCTION_NAME:-OnlineInfer}
export BATCH_INFER_FUNCTION_NAME=${BATCH_INFER_FUNCTION_NAME:-BatchInfer}

# Get API Gateway URL
API_ID=$(aws apigateway get-rest-apis --region $AWS_REGION --query "items[?name=='MLPipelineInferenceAPI'].id" --output text 2>/dev/null || echo "")
if [ -z "$API_ID" ] || [ "$API_ID" == "None" ]; then
    echo "❌ API Gateway not found. Please run setup_inference.sh first."
    exit 1
fi

API_URL="https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/prod/predict"
echo "API Endpoint: $API_URL"
echo ""

# Test 1: Online Inference via Lambda (direct)
echo "Test 1: Online Inference via Lambda (direct invocation)"
echo "---------------------------------------------------"
cat > /tmp/online-infer-payload.json <<EOF
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

echo "Invoking Lambda function directly..."
aws lambda invoke \
  --function-name $ONLINE_INFER_FUNCTION_NAME \
  --cli-binary-format raw-in-base64-out \
  --payload file:///tmp/online-infer-payload.json \
  /tmp/online-infer-response.json \
  --region $AWS_REGION

echo "Response:"
cat /tmp/online-infer-response.json | python3 -m json.tool
echo ""

# Test 2: Online Inference via API Gateway
echo "Test 2: Online Inference via API Gateway"
echo "---------------------------------------------------"
echo "Sending POST request to API Gateway..."
curl -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -d @/tmp/online-infer-payload.json \
  -w "\nHTTP Status: %{http_code}\n" \
  -s

echo ""
echo ""

# Test 3: Batch Inference
echo "Test 3: Batch Inference"
echo "---------------------------------------------------"
echo "Creating sample batch inference input file..."

cat > /tmp/batch_inference_input.csv <<EOF
area_sqm,latitude,longitude,house_age,med_inc,ave_rooms,ave_bedrms,population,ave_occup,house_type
150.5,37.7749,-122.4194,25,4.5,5.0,2.0,1000,3.0,house
200.0,37.7849,-122.4094,30,5.0,6.0,3.0,1500,3.5,apartment
120.0,37.7649,-122.4294,20,3.5,4.0,2.0,800,2.5,townhouse
EOF

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BATCH_FILE="batch_test_${TIMESTAMP}.csv"

echo "Uploading to S3 to_infer/ prefix (this will trigger batch inference)..."
aws s3 cp /tmp/batch_inference_input.csv "s3://$BUCKET/to_infer/$BATCH_FILE" --region $AWS_REGION

echo "✓ File uploaded: s3://$BUCKET/to_infer/$BATCH_FILE"
echo ""
echo "Note: Batch inference is triggered automatically by EventBridge."
echo "It may take a few minutes to process. Monitor with:"
echo "  aws sagemaker list-transform-jobs --name-contains batch-infer --region $AWS_REGION"
echo ""
echo "Check output in S3:"
echo "  aws s3 ls s3://$BUCKET/predicted/ --recursive"
echo ""

# Test 4: Check Endpoint Status
echo "Test 4: Check SageMaker Endpoint Status"
echo "---------------------------------------------------"
aws sagemaker describe-endpoint \
  --endpoint-name $ENDPOINT_NAME \
  --region $AWS_REGION \
  --query '{Name:EndpointName,Status:EndpointStatus,Config:EndpointConfigName,CreationTime:CreationTime}' \
  --output json | python3 -m json.tool

echo ""
echo "=========================================="
echo "Testing Complete!"
echo "=========================================="

