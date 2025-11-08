# AWS Setup Guide: Data Ingestion & Cleaning Pipeline

## Overview
This guide sets up an automated data cleaning pipeline that triggers when files are uploaded to S3. The pipeline uses:
- **EventBridge** to detect S3 uploads
- **Lambda** (container image) to clean and transform data
- **S3** for input (`raw/`) and output (`processed/`)

## Prerequisites
- AWS CLI configured with admin access
- Docker installed (for building Lambda container image)
- S3 bucket created (globally unique name)

## Step-by-Step Setup

### 1. Set Environment Variables
```bash
export AWS_PROFILE=helen1
export AWS_REGION=us-west-2
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export BUCKET=ml-pipeline-dev-954976298878-us-west-2-helen  # Your unique bucket name
export LAMBDA_ROLE_NAME=CleanTransformLambdaRole
export FUNCTION_NAME=CleanTransform
export REPO=clean-transform
```

### 2. Create IAM Role for Lambda
```bash
# Create trust policy
cat > /tmp/lambda-trust-policy.json <<EOF
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
  --role-name $LAMBDA_ROLE_NAME \
  --assume-role-policy-document file:///tmp/lambda-trust-policy.json

# Attach basic Lambda execution policy
aws iam attach-role-policy \
  --role-name $LAMBDA_ROLE_NAME \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# Create S3 access policy
cat > /tmp/s3-lambda-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::${BUCKET}/raw/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject"
      ],
      "Resource": "arn:aws:s3:::${BUCKET}/processed/*"
    }
  ]
}
EOF

# Attach S3 policy to role
aws iam put-role-policy \
  --role-name $LAMBDA_ROLE_NAME \
  --policy-name S3AccessPolicy \
  --policy-document file:///tmp/s3-lambda-policy.json

# Get role ARN
export ROLE_ARN=$(aws iam get-role --role-name $LAMBDA_ROLE_NAME --query 'Role.Arn' --output text)
echo "Role ARN: $ROLE_ARN"
```

### 3. Build and Push Lambda Container Image
```bash
# Create ECR repository
aws ecr create-repository --repository-name $REPO 2>/dev/null || echo "Repo already exists"

# Login to ECR
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com



### 4. Create Lambda Function
```bash
aws lambda create-function \
  --function-name $FUNCTION_NAME \
  --package-type Image \
  --code ImageUri=$IMAGE_URI \
  --role $ROLE_ARN \
  --architectures arm64 \
  --memory-size 1024 \
  --timeout 300 \
  --environment "Variables={BUCKET=$BUCKET,OUTPUT_PREFIX=processed/}" \
  --description "Cleans housing transaction data from S3 raw/ to processed/"
```

### 5. Update Lambda Function Code (After Code Changes)
```bash
# Rebuild and push updated image
docker buildx build \
  --platform linux/arm64 \
  --provenance=false \
  --sbom=false \
  --push \
  -t ${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO}:latest \
  .

# Update Lambda to use new image
aws lambda update-function-code \
  --function-name $FUNCTION_NAME \
  --image-uri ${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO}:latest

```

### 6. Configure S3 Event Notification (EventBridge)
```bash
# Create EventBridge rule for S3 uploads
aws events put-rule \
  --name s3-raw-upload-trigger \
  --event-pattern '{
    "source": ["aws.s3"],
    "detail-type": ["Object Created"],
    "detail": {
      "bucket": {
        "name": ["'$BUCKET'"]
      },
      "object": {
        "key": [{
          "prefix": "raw/"
        }]
      }
    }
  }' \
  --state ENABLED

# Add Lambda as target
aws events put-targets \
  --rule s3-raw-upload-trigger \
  --targets "Id=1,Arn=arn:aws:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${FUNCTION_NAME}"

# Grant EventBridge permission to invoke Lambda
aws lambda add-permission \
  --function-name $FUNCTION_NAME \
  --statement-id eventbridge-trigger \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn arn:aws:events:${AWS_REGION}:${ACCOUNT_ID}:rule/s3-raw-upload-trigger
```

### 7. Enable S3 EventBridge Notifications
```bash
# Enable EventBridge notifications on S3 bucket
aws s3api put-bucket-notification-configuration \
  --bucket $BUCKET \
  --notification-configuration '{
    "EventBridgeConfiguration": {}
  }'
```


## Feature Engineering Lambda Setup

### 10. Create IAM Role for Feature Engineering Lambda
```bash
export FEATURE_ROLE_NAME=FeatureBuildLambdaRole
export FEATURE_FUNCTION_NAME=FeatureBuild

# Create trust policy
cat > /tmp/feature-lambda-trust-policy.json <<EOF
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
  --role-name $FEATURE_ROLE_NAME \
  --assume-role-policy-document file:///tmp/feature-lambda-trust-policy.json

# Attach basic Lambda execution policy
aws iam attach-role-policy \
  --role-name $FEATURE_ROLE_NAME \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# Create S3 access policy for feature Lambda
cat > /tmp/feature-s3-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::${BUCKET}/processed/*"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject"],
      "Resource": "arn:aws:s3:::${BUCKET}/feature_store/*"
    }
  ]
}
EOF

# Attach S3 policy to role
aws iam put-role-policy \
  --role-name $FEATURE_ROLE_NAME \
  --policy-name S3AccessPolicy \
  --policy-document file:///tmp/feature-s3-policy.json

# Get role ARN
export FEATURE_ROLE_ARN=$(aws iam get-role --role-name $FEATURE_ROLE_NAME --query 'Role.Arn' --output text)
```

### 11. Build and Push Feature Engineering Lambda Image
```bash
export FEATURE_REPO=feature-build

# Create ECR repository
aws ecr create-repository --repository-name $FEATURE_REPO 2>/dev/null || echo "Repo already exists"

# Build and push
docker buildx build \
  --platform linux/arm64 \
  --provenance=false \
  --sbom=false \
  --push \
  -f Dockerfile.feature \
  -t ${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${FEATURE_REPO}:latest \
  .

export FEATURE_IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${FEATURE_REPO}:latest"
```

### 12. Create Feature Engineering Lambda Function
```bash
aws lambda create-function \
  --function-name $FEATURE_FUNCTION_NAME \
  --package-type Image \
  --code ImageUri=$FEATURE_IMAGE_URI \
  --role $FEATURE_ROLE_ARN \
  --architectures arm64 \
  --memory-size 1024 \
  --timeout 300 \
  --environment "Variables={BUCKET=$BUCKET,OUTPUT_PREFIX=feature_store/}" \
  --description "Feature engineering: distance, log transforms, one-hot encoding"
```

### 13. Configure EventBridge to Trigger Feature Lambda
```bash
# Create EventBridge rule for processed/ uploads
aws events put-rule \
  --name s3-processed-upload-trigger \
  --event-pattern '{
    "source": ["aws.s3"],
    "detail-type": ["Object Created"],
    "detail": {
      "bucket": {
        "name": ["'$BUCKET'"]
      },
      "object": {
        "key": [{
          "prefix": "processed/"
        }]
      }
    }
  }' \
  --state ENABLED

# Add Lambda as target
aws events put-targets \
  --rule s3-processed-upload-trigger \
  --targets "Id=1,Arn=arn:aws:lambda:${AWS_REGION}:${ACCOUNT_ID}:function:${FEATURE_FUNCTION_NAME}"

# Grant EventBridge permission
aws lambda add-permission \
  --function-name $FEATURE_FUNCTION_NAME \
  --statement-id eventbridge-feature-trigger \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn arn:aws:events:${AWS_REGION}:${ACCOUNT_ID}:rule/s3-processed-upload-trigger
```

## Model Training Setup

### 14. Create SageMaker Execution Role
```bash
export SAGEMAKER_ROLE_NAME=SageMakerExecutionRole

# Create trust policy
cat > /tmp/sagemaker-trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "sagemaker.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

# Create the role
aws iam create-role \
  --role-name $SAGEMAKER_ROLE_NAME \
  --assume-role-policy-document file:///tmp/sagemaker-trust-policy.json

# Attach SageMaker execution policy
aws iam attach-role-policy \
  --role-name $SAGEMAKER_ROLE_NAME \
  --policy-arn arn:aws:iam::aws:policy/AmazonSageMakerFullAccess

# Create S3 access policy for SageMaker
cat > /tmp/sagemaker-s3-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": [
        "arn:aws:s3:::${BUCKET}/feature_store/*",
        "arn:aws:s3:::${BUCKET}/models/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket"
      ],
      "Resource": "arn:aws:s3:::${BUCKET}"
    }
  ]
}
EOF

# Attach S3 policy
aws iam put-role-policy \
  --role-name $SAGEMAKER_ROLE_NAME \
  --policy-name S3AccessPolicy \
  --policy-document file:///tmp/sagemaker-s3-policy.json

export SAGEMAKER_ROLE_ARN=$(aws iam get-role --role-name $SAGEMAKER_ROLE_NAME --query 'Role.Arn' --output text)
```

### 15. Create Trigger Training Lambda
```bash
export TRAINING_ROLE_NAME=TriggerTrainingLambdaRole
export TRAINING_FUNCTION_NAME=TriggerTraining

# Create IAM role for training trigger Lambda
cat > /tmp/training-lambda-trust-policy.json <<EOF
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

aws iam create-role \
  --role-name $TRAINING_ROLE_NAME \
  --assume-role-policy-document file:///tmp/training-lambda-trust-policy.json

aws iam attach-role-policy \
  --role-name $TRAINING_ROLE_NAME \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# SageMaker access policy
cat > /tmp/training-sagemaker-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sagemaker:CreateTrainingJob",
        "sagemaker:DescribeTrainingJob"
      ],
      "Resource": "*"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name $TRAINING_ROLE_NAME \
  --policy-name SageMakerAccessPolicy \
  --policy-document file:///tmp/training-sagemaker-policy.json

export TRAINING_ROLE_ARN=$(aws iam get-role --role-name $TRAINING_ROLE_NAME --query 'Role.Arn' --output text)

# Package Lambda (zip deployment for simple Lambda)
cd lambdas/trigger_training
zip -r function.zip handler.py
cd ../..

# Create Lambda function
aws lambda create-function \
  --function-name $TRAINING_FUNCTION_NAME \
  --runtime python3.11 \
  --role $TRAINING_ROLE_ARN \
  --handler handler.lambda_handler \
  --zip-file fileb://lambdas/trigger_training/function.zip \
  --timeout 300 \
  --memory-size 256 \
  --environment "Variables={SAGEMAKER_ROLE_ARN=$SAGEMAKER_ROLE_ARN,BUCKET=$BUCKET}"
```

### 16. Create Step Functions State Machine
```bash
# Create state machine from definition
aws stepfunctions create-state-machine \
  --name MLPipelineStateMachine \
  --definition file://cdk/step_functions_definition.json \
  --role-arn arn:aws:iam::${ACCOUNT_ID}:role/StepFunctionsExecutionRole

# Note: You'll need to create StepFunctionsExecutionRole with permissions to invoke Lambdas and SageMaker
```

### 17. Test Training Job (Manual)
```bash
# Manually trigger training job for testing
aws lambda invoke \
  --function-name $TRAINING_FUNCTION_NAME \
  --payload '{"bucket":"'$BUCKET'","feature_key":"feature_store/california_housing1_features.csv"}' \
  /tmp/training-response.json

cat /tmp/training-response.json

# Monitor training job
aws sagemaker describe-training-job --training-job-name <job-name-from-response>
```

## After this setup, the pipeline will:
- ✅ Automatically trigger when files are uploaded to `s3://$BUCKET/raw/`
- ✅ Clean data (remove price=0, standardize units) → `processed/`
- ✅ Automatically trigger feature engineering when cleaned data is ready
- ✅ Build features (distance, log transforms, one-hot encoding) → `feature_store/`
- ✅ Trigger SageMaker training job when features are ready
- ✅ Train XGBoost model and save to `s3://$BUCKET/models/`

