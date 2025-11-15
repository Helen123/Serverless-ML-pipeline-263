# AWS Setup Guide: Model Registration & Deployment

## Overview
This guide sets up model registration (DynamoDB) and deployment (SageMaker endpoints) for the ML pipeline:
- **DynamoDB Table** for storing model metadata
- **Register Model Lambda** to write model metadata after training completes
- **Deploy Model Lambda** to create SageMaker models and endpoints

## Prerequisites
- AWS CLI configured with admin access
- SageMaker training job completed (model artifact in S3)
- S3 bucket with `models/` prefix

## Step-by-Step Setup

### 1. Set Environment Variables
```bash
export AWS_PROFILE=helen1
export AWS_REGION=us-west-2
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export BUCKET=ml-pipeline-dev-954976298878-us-west-2-helen
export MODEL_TABLE_NAME=ml-pipeline-models
export REGISTER_FUNCTION_NAME=RegisterModel
export DEPLOY_FUNCTION_NAME=DeployModel
```

### 2. Create DynamoDB Table
```bash
aws dynamodb create-table \
  --table-name $MODEL_TABLE_NAME \
  --attribute-definitions \
    AttributeName=model_id,AttributeType=S \
  --key-schema \
    AttributeName=model_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region $AWS_REGION

# Wait for table to be active
aws dynamodb wait table-exists --table-name $MODEL_TABLE_NAME --region $AWS_REGION
```

### 3. Create IAM Role for Register Model Lambda
```bash
# Create trust policy
cat > /tmp/register-lambda-trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

# Create the role
aws iam create-role \
  --role-name RegisterModelLambdaRole \
  --assume-role-policy-document file:///tmp/register-lambda-trust-policy.json

# Attach basic Lambda execution policy
aws iam attach-role-policy \
  --role-name RegisterModelLambdaRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# Create policy for DynamoDB and SageMaker access
cat > /tmp/register-lambda-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:UpdateItem"
      ],
      "Resource": "arn:aws:dynamodb:${AWS_REGION}:${ACCOUNT_ID}:table/${MODEL_TABLE_NAME}"
    },
    {
      "Effect": "Allow",
      "Action": [
        "sagemaker:DescribeTrainingJob"
      ],
      "Resource": "*"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name RegisterModelLambdaRole \
  --policy-name RegisterModelLambdaPolicy \
  --policy-document file:///tmp/register-lambda-policy.json

# Get role ARN
export REGISTER_ROLE_ARN=$(aws iam get-role --role-name RegisterModelLambdaRole --query 'Role.Arn' --output text)
```

### 4. Create IAM Role for Deploy Model Lambda
```bash
# Create trust policy
cat > /tmp/deploy-lambda-trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

# Create the role
aws iam create-role \
  --role-name DeployModelLambdaRole \
  --assume-role-policy-document file:///tmp/deploy-lambda-trust-policy.json

# Attach basic Lambda execution policy
aws iam attach-role-policy \
  --role-name DeployModelLambdaRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# Create policy for DynamoDB, SageMaker, and IAM PassRole
cat > /tmp/deploy-lambda-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:UpdateItem"
      ],
      "Resource": "arn:aws:dynamodb:${AWS_REGION}:${ACCOUNT_ID}:table/${MODEL_TABLE_NAME}"
    },
    {
      "Effect": "Allow",
      "Action": [
        "sagemaker:CreateModel",
        "sagemaker:DescribeModel",
        "sagemaker:CreateEndpointConfig",
        "sagemaker:DescribeEndpointConfig",
        "sagemaker:CreateEndpoint",
        "sagemaker:DescribeEndpoint",
        "sagemaker:UpdateEndpoint"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "iam:PassRole"
      ],
      "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/SageMakerExecutionRole"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name DeployModelLambdaRole \
  --policy-name DeployModelLambdaPolicy \
  --policy-document file:///tmp/deploy-lambda-policy.json

# Get role ARN
export DEPLOY_ROLE_ARN=$(aws iam get-role --role-name DeployModelLambdaRole --query 'Role.Arn' --output text)
```

### 5. Package and Deploy Register Model Lambda
```bash
cd lambdas/register_model
zip -r function.zip handler.py
cd ../..

# Create Lambda function
aws lambda create-function \
  --function-name $REGISTER_FUNCTION_NAME \
  --runtime python3.11 \
  --role $REGISTER_ROLE_ARN \
  --handler handler.lambda_handler \
  --zip-file fileb://lambdas/register_model/function.zip \
  --timeout 300 \
  --memory-size 256 \
  --environment "Variables={MODEL_TABLE_NAME=$MODEL_TABLE_NAME}" \
  --description "Registers model metadata to DynamoDB after training completes" \
  --region $AWS_REGION
```

### 6. Package and Deploy Deploy Model Lambda
```bash
cd lambdas/deploy_model
zip -r function.zip handler.py
cd ../..

# Create Lambda function
aws lambda create-function \
  --function-name $DEPLOY_FUNCTION_NAME \
  --runtime python3.11 \
  --role $DEPLOY_ROLE_ARN \
  --handler handler.lambda_handler \
  --zip-file fileb://lambdas/deploy_model/function.zip \
  --timeout 600 \
  --memory-size 512 \
  --environment "Variables={MODEL_TABLE_NAME=$MODEL_TABLE_NAME}" \
  --description "Deploys model to SageMaker endpoint" \
  --region $AWS_REGION
```

### 7. Test Model Registration
```bash
# Test with a completed training job
cat > /tmp/register-payload.json <<EOF
{
  "training_job_name": "house-price-training-20251108-032257",
  "model_name": "house-price-model-v1",
  "version": "1.0.0"
}
EOF

aws lambda invoke \
  --function-name $REGISTER_FUNCTION_NAME \
  --cli-binary-format raw-in-base64-out \
  --payload file:///tmp/register-payload.json \
  /tmp/register-response.json

cat /tmp/register-response.json | python3 -m json.tool

# Verify in DynamoDB
aws dynamodb get-item \
  --table-name $MODEL_TABLE_NAME \
  --key '{"model_id": {"S": "house-price-model-v1-1.0.0"}}' \
  --region $AWS_REGION
```

### 8. Test Model Deployment
```bash
# Option 1: Deploy using model_id from DynamoDB
cat > /tmp/deploy-payload.json <<EOF
{
  "model_id": "house-price-model-v1-1.0.0",
  "endpoint_name": "house-price-endpoint",
  "instance_type": "ml.m5.large",
  "initial_instance_count": 1
}
EOF

# Option 2: Deploy directly with S3 URI (without DynamoDB)
cat > /tmp/deploy-payload-direct.json <<EOF
{
  "model_artifact_s3_uri": "s3://${BUCKET}/models/house-price-training-20251108-032257/output/model.tar.gz",
  "image_uri": "246618743249.dkr.ecr.us-west-2.amazonaws.com/sagemaker-xgboost:1.5-1",
  "endpoint_name": "house-price-endpoint",
  "instance_type": "ml.m5.large",
  "initial_instance_count": 1
}
EOF

aws lambda invoke \
  --function-name $DEPLOY_FUNCTION_NAME \
  --cli-binary-format raw-in-base64-out \
  --payload file:///tmp/deploy-payload.json \
  /tmp/deploy-response.json

cat /tmp/deploy-response.json | python3 -m json.tool

# Check endpoint status
aws sagemaker describe-endpoint \
  --endpoint-name house-price-endpoint \
  --region $AWS_REGION
```

### 9. Monitor Endpoint Creation
```bash
# Wait for endpoint to be in service (this can take 5-10 minutes)
aws sagemaker wait endpoint-in-service \
  --endpoint-name house-price-endpoint \
  --region $AWS_REGION

# Check endpoint status
aws sagemaker describe-endpoint \
  --endpoint-name house-price-endpoint \
  --query 'EndpointStatus' \
  --output text \
  --region $AWS_REGION
```

## Integration with Step Functions

To integrate with the pipeline, update `cdk/step_functions_definition.json` to add registration and deployment steps after training:

```json
{
  "TriggerTraining": {
    "Type": "Task",
    "Resource": "arn:aws:states:::lambda:invoke",
    "Parameters": {
      "FunctionName": "TriggerTraining",
      "Payload": {
        "bucket.$": "$.feature_result.Payload.bucket",
        "feature_key.$": "$.feature_result.Payload.features_key"
      }
    },
    "ResultPath": "$.training_result",
    "Next": "WaitForTraining"
  },
  "WaitForTraining": {
    "Type": "Task",
    "Resource": "arn:aws:states:::sagemaker:waitForTrainingJob",
    "Parameters": {
      "TrainingJobName.$": "$.training_result.Payload.training_job_name"
    },
    "ResultPath": "$.training_complete",
    "Next": "RegisterModel"
  },
  "RegisterModel": {
    "Type": "Task",
    "Resource": "arn:aws:states:::lambda:invoke",
    "Parameters": {
      "FunctionName": "RegisterModel",
      "Payload": {
        "training_job_name.$": "$.training_complete.TrainingJobName"
      }
    },
    "ResultPath": "$.registration_result",
    "Next": "DeployModel"
  },
  "DeployModel": {
    "Type": "Task",
    "Resource": "arn:aws:states:::lambda:invoke",
    "Parameters": {
      "FunctionName": "DeployModel",
      "Payload": {
        "model_id.$": "$.registration_result.Payload.model_id",
        "endpoint_name": "house-price-endpoint"
      }
    },
    "ResultPath": "$.deployment_result",
    "End": true
  }
}
```

## Troubleshooting

### DynamoDB Access Denied
- Ensure the Lambda execution role has `dynamodb:PutItem` permission on the table
- Check the table name matches the environment variable

### SageMaker Access Denied
- Ensure the Lambda execution role has `iam:PassRole` permission for `SageMakerExecutionRole`
- Verify `SageMakerExecutionRole` has S3 access to the model artifact bucket

### Endpoint Creation Fails
- Check service quotas for the instance type (e.g., `ml.m5.large`)
- Verify the model artifact S3 URI is accessible
- Check CloudWatch logs for detailed error messages

### Model Not Found in DynamoDB
- Verify the model was registered successfully (check Lambda logs)
- Ensure the `model_id` matches exactly (case-sensitive)

## Next Steps

1. **Update Step Functions** to include registration and deployment steps
2. **Set up CloudWatch Alarms** for endpoint health monitoring
3. **Create inference Lambda** for online predictions via API Gateway
4. **Implement model versioning** strategy in DynamoDB

