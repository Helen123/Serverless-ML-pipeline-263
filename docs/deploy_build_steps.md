# Build & Deploy Checklist (AWS)

## 0) Prereqs (once per machine)
- Install AWS CLI v2 and Docker.
- Configure a profile: `aws configure --profile helen1` (region `us-west-2`, json).
- Export env when working:
  ```bash
  export AWS_PROFILE=helen1
  export AWS_REGION=us-west-2
  export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
  export BUCKET=ml-pipeline-dev-${ACCOUNT_ID}-us-west-2-helen
  ```

## 1) Create S3 bucket + prefixes (once)
```bash
aws s3 mb s3://$BUCKET --region $AWS_REGION
aws s3api put-bucket-versioning --bucket $BUCKET --versioning-configuration Status=Enabled --region $AWS_REGION
aws s3api put-object --bucket $BUCKET --key raw/ --region $AWS_REGION
aws s3api put-object --bucket $BUCKET --key processed/ --region $AWS_REGION
aws s3api put-object --bucket $BUCKET --key feature_store/ --region $AWS_REGION
aws s3api put-object --bucket $BUCKET --key models/ --region $AWS_REGION
aws s3api put-object --bucket $BUCKET --key to_infer/ --region $AWS_REGION
aws s3api put-object --bucket $BUCKET --key predicted/ --region $AWS_REGION
aws s3api put-object --bucket $BUCKET --key temp_batch/ --region $AWS_REGION
```
Enable S3→EventBridge:
```bash
aws s3api put-bucket-notification-configuration --bucket $BUCKET --notification-configuration '{"EventBridgeConfiguration":{}}'
```

## 2) IAM roles (once)
- Lambda execution roles: `CleanTransformLambdaRole`, `FeatureBuildLambdaRole`, `TriggerTrainingLambdaRole`, `RegisterModelLambdaRole`, `DeployModelLambdaRole`, `OnlineInferLambdaRole`, `BatchInferLambdaRole` with:
  - `AWSLambdaBasicExecutionRole`
  - S3 read/write for relevant prefixes
  - For TriggerTraining: `sagemaker:CreateTrainingJob`, `iam:PassRole` to SageMaker execution role
  - For RegisterModel: `sagemaker:DescribeTrainingJob`, DynamoDB write, `lambda:InvokeFunction` (DeployModel)
  - For DeployModel: `sagemaker:*` for model/endpoint creation
  - For OnlineInfer/BatchInfer: `sagemaker:InvokeEndpoint`, `sagemaker:DescribeEndpoint*`
- SageMaker execution role: allow `s3:GetObject/PutObject` on your bucket, `ecr:*` pull, CloudWatch logs.

## 3) ECR repos (once)
```bash
aws ecr create-repository --repository-name clean-transform
aws ecr create-repository --repository-name feature-build
aws ecr create-repository --repository-name trigger-training
aws ecr create-repository --repository-name register-model
aws ecr create-repository --repository-name deploy-model
aws ecr create-repository --repository-name online-infer
aws ecr create-repository --repository-name batch-infer
```

## 4) Build & push Lambda container images
Use `--platform linux/arm64 --provenance=false --sbom=false` to avoid OCI index.
Example (clean-transform):
```bash
docker buildx build --platform linux/arm64 --provenance=false --sbom=false --push \
  -t ${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/clean-transform:latest \
  -f Dockerfile .
```
Repeat for each Lambda with its Dockerfile (e.g., `Dockerfile.feature`, `Dockerfile.batch_infer`, etc.).

## 5) Create/Update Lambda functions
Example:
```bash
aws lambda create-function \
  --function-name CleanTransform \
  --package-type Image \
  --code ImageUri=${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/clean-transform:latest \
  --role arn:aws:iam::${ACCOUNT_ID}:role/CleanTransformLambdaRole \
  --architectures arm64 \
  --memory-size 1024 \
  --timeout 300 \
  --environment "Variables={BUCKET=$BUCKET,OUTPUT_PREFIX=processed/}" \
  --region $AWS_REGION
```
Use `update-function-code` after new image pushes.

## 6) DynamoDB table (once)
```bash
aws dynamodb create-table \
  --table-name ml-pipeline-models \
  --attribute-definitions AttributeName=model_id,AttributeType=S \
  --key-schema AttributeName=model_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region $AWS_REGION
```

## 7) EventBridge rules (S3 triggers)
- raw/ -> CleanTransform
- processed/ -> FeatureBuild
- feature_store/ -> TriggerTraining
- models/ -> RegisterModel
- to_infer/ -> BatchInfer

Example (feature_store -> TriggerTraining):
```bash
RULE_NAME=s3-feature-upload-trigger
aws events put-rule --name $RULE_NAME --event-pattern "{\"source\":[\"aws.s3\"],\"detail-type\":[\"Object Created\"],\"detail\":{\"bucket\":{\"name\":[\"$BUCKET\"]},\"object\":{\"key\":[{\"prefix\":\"feature_store/\"}]}}}" --state ENABLED --region $AWS_REGION
FUNCTION_ARN=$(aws lambda get-function --function-name TriggerTraining --region $AWS_REGION --query 'Configuration.FunctionArn' --output text)
aws events put-targets --rule $RULE_NAME --region $AWS_REGION --targets "Id=1,Arn=$FUNCTION_ARN"
aws lambda add-permission --function-name TriggerTraining --statement-id allow-eventbridge-feature --action lambda:InvokeFunction --principal events.amazonaws.com --source-arn arn:aws:events:$AWS_REGION:$ACCOUNT_ID:rule/$RULE_NAME --region $AWS_REGION
```
Repeat for other prefixes/rules.

## 8) API Gateway for online inference
- Create REST API, resource `/predict`, POST integration with `OnlineInfer` Lambda.
- Enable CORS if needed.
- Ensure API Gateway can invoke the Lambda (usually auto).

## 9) Train & auto-register/deploy flow
- Upload feature data to `feature_store/` to trigger `TriggerTraining`.
- Training writes model artifact to `models/`.
- `RegisterModel` writes metadata to DynamoDB and invokes `DeployModel`.
- `DeployModel` creates/updates serverless endpoint (e.g., `house-price-endpoint`) and records endpoint info.

## 10) Batch inference
- Upload CSV to `to_infer/` → EventBridge → `BatchInfer` → SageMaker Batch Transform → outputs to `predicted/`.
- `BatchInfer` handles feature-engineered inputs and excludes the target column (14 features only).

