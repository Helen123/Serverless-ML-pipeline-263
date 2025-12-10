"""
Lambda Handler to Register Model Metadata to DynamoDB
Called after training job completes to store model information
"""

import json
import os
import boto3
import logging
from datetime import datetime
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
sagemaker_client = boto3.client('sagemaker')

def lambda_handler(event, context):
    """
    Register model metadata to DynamoDB
    
    Handles two event types:
    1. EventBridge S3 event (from models/ upload trigger)
    2. Manual invocation with explicit training_job_name
    
    Expected event formats:
    - EventBridge S3: {detail: {bucket: {...}, object: {key: "models/{training_job_name}/output/model.tar.gz"}}}
    - Manual: {"training_job_name": "house-price-training-20251108-032257", ...}
    """
    
    try:
        # Get table name from environment or use default
        table_name = os.environ.get('MODEL_TABLE_NAME', 'ml-pipeline-models')
        table = dynamodb.Table(table_name)
        
        # Parse event - handle EventBridge S3 event format
        training_job_name = event.get('training_job_name')
        
        if not training_job_name:
            # Try to extract from EventBridge S3 event
            if 'detail' in event and 'source' in event and event.get('source') == 'aws.s3':
                # EventBridge S3 event format
                detail = event['detail']
                # EventBridge S3 events have: detail.object.key
                if 'object' in detail and 'key' in detail['object']:
                    s3_key = detail['object']['key']
                    logger.info(f"Received EventBridge S3 event for: {s3_key}")
                else:
                    raise ValueError(f"Invalid EventBridge S3 event format: missing object.key in detail")
                
                # Extract training_job_name from S3 key
                # Format: models/{training_job_name}/output/model.tar.gz
                # We only process files in the output/ directory (model artifacts)
                if s3_key.startswith('models/') and '/output/' in s3_key:
                    # Extract: models/{training_job_name}/output/{filename}
                    # Split by '/' and get index 1 (training_job_name)
                    parts = s3_key.split('/')
                    if len(parts) >= 3 and parts[2] == 'output':
                        training_job_name = parts[1]  # Extract training_job_name
                        logger.info(f"Extracted training_job_name from S3 key: {training_job_name}")
                    else:
                        raise ValueError(f"Invalid S3 key format: {s3_key}. Expected: models/{{training_job_name}}/output/{{filename}}")
                else:
                    logger.info(f"Skipping file not in models/.../output/ directory: {s3_key}")
                    return {
                        'statusCode': 200,
                        'message': f'Skipped: {s3_key} (not a model artifact in output/)'
                    }
            else:
                raise ValueError("Missing 'training_job_name' in event. Event must be EventBridge S3 event or manual invocation with 'training_job_name' field.")
        
        logger.info(f"Registering model for training job: {training_job_name}")
        
        # Get model artifact S3 URI from event if available (faster, avoids timing issues)
        model_artifact_s3_uri = None
        if 'detail' in event and 'object' in event['detail']:
            bucket_name = event['detail'].get('bucket', {}).get('name', '')
            s3_key = event['detail']['object'].get('key', '')
            if bucket_name and s3_key:
                model_artifact_s3_uri = f"s3://{bucket_name}/{s3_key}"
                logger.info(f"Using model artifact S3 URI from event: {model_artifact_s3_uri}")
        
        # Get training job details from SageMaker
        # Add retry logic to ensure we get the latest status (training might have just completed)
        import time
        max_retries = 3
        retry_delay = 2
        training_job = None
        
        for attempt in range(max_retries):
            training_job = sagemaker_client.describe_training_job(
                TrainingJobName=training_job_name
            )
            
            # If we don't have S3 URI from event, try to get it from training job
            if not model_artifact_s3_uri:
                model_artifact_s3_uri = training_job.get('ModelArtifacts', {}).get('S3ModelArtifacts')
            
            # Check if training is completed and model artifacts are available
            training_status = training_job.get('TrainingJobStatus', 'Unknown')
            if training_status == 'Completed' and model_artifact_s3_uri:
                logger.info(f"Training job completed, model artifacts available")
                break
            elif attempt < max_retries - 1:
                logger.info(f"Training status: {training_status}, waiting {retry_delay}s before retry...")
                time.sleep(retry_delay)
            else:
                if not model_artifact_s3_uri:
                    raise ValueError(f"No model artifacts found for training job: {training_job_name} after {max_retries} retries")
        
        # Final check
        if not model_artifact_s3_uri:
            model_artifact_s3_uri = training_job.get('ModelArtifacts', {}).get('S3ModelArtifacts')
            if not model_artifact_s3_uri:
                raise ValueError(f"No model artifacts found for training job: {training_job_name}")
        
        # Extract metrics (RMSE)
        final_metrics = training_job.get('FinalMetricDataList', [])
        train_rmse = None
        for metric in final_metrics:
            if metric.get('MetricName') == 'train:rmse':
                train_rmse = float(metric.get('Value', 0))
                break
        
        # Generate model ID and version
        # Use training job completion time or current time for version
        training_end_time = training_job.get('TrainingEndTime')
        if training_end_time:
            # Use training end time for version (more accurate)
            version_timestamp = training_end_time.strftime('%Y%m%d-%H%M%S') if hasattr(training_end_time, 'strftime') else datetime.fromisoformat(str(training_end_time).replace('Z', '+00:00')).strftime('%Y%m%d-%H%M%S')
        else:
            # Fallback to current time
            version_timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        
        model_name = event.get('model_name', f"house-price-model-{datetime.now().strftime('%Y%m%d')}")
        model_version = event.get('version', version_timestamp)
        model_id = f"{model_name}-{model_version}"
        
        # Extract hyperparameters
        hyperparameters = training_job.get('HyperParameters', {})
        
        # Extract training configuration
        resource_config = training_job.get('ResourceConfig', {})
        instance_type = resource_config.get('InstanceType', 'unknown')
        
        # Create model metadata item
        model_item = {
            'model_id': model_id,
            'model_name': model_name,
            'version': model_version,
            'training_job_name': training_job_name,
            'training_job_arn': training_job.get('TrainingJobArn', ''),
            'model_artifact_s3_uri': model_artifact_s3_uri,
            'training_status': training_job.get('TrainingJobStatus', 'Unknown'),
            'train_rmse': Decimal(str(train_rmse)) if train_rmse is not None else None,
            'hyperparameters': json.dumps(hyperparameters),
            'instance_type': instance_type,
            'created_at': datetime.utcnow().isoformat(),
            'created_timestamp': int(datetime.utcnow().timestamp()),
            'is_deployed': False,
            'endpoint_name': None
        }
        
        # Add optional fields
        if 'description' in event:
            model_item['description'] = event['description']
        
        # Write to DynamoDB
        logger.info(f"Writing model metadata to DynamoDB: {model_id}")
        table.put_item(Item=model_item)
        
        logger.info(f"Model registered successfully: {model_id}")
        
        # Automatically trigger deployment
        try:
            lambda_client = boto3.client('lambda')
            deploy_function_name = os.environ.get('DEPLOY_FUNCTION_NAME', 'DeployModel')
            
            logger.info(f"Triggering automatic deployment: {deploy_function_name}")
            deploy_payload = {
                'model_id': model_id,
                'endpoint_name': event.get('endpoint_name', 'house-price-endpoint'),
                'use_serverless': event.get('use_serverless', True)  # Default to serverless
            }
            
            deploy_response = lambda_client.invoke(
                FunctionName=deploy_function_name,
                InvocationType='Event',  # Async invocation
                Payload=json.dumps(deploy_payload)
            )
            
            logger.info(f"Deployment triggered: {deploy_response.get('StatusCode')}")
        except Exception as deploy_error:
            # Don't fail registration if deployment fails
            logger.warning(f"Failed to trigger automatic deployment: {str(deploy_error)}")
            logger.warning("Model registered but deployment must be triggered manually")
        
        return {
            'statusCode': 200,
            'model_id': model_id,
            'model_name': model_name,
            'version': model_version,
            'model_artifact_s3_uri': model_artifact_s3_uri,
            'train_rmse': train_rmse,
            'dynamodb_table': table_name,
            'deployment_triggered': True
        }
        
    except Exception as e:
        logger.error(f"Error registering model: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'statusCode': 500,
            'error': str(e)
        }

