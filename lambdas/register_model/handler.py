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
    
    Expected event:
    {
        "training_job_name": "house-price-training-20251108-032257",
        "bucket": "ml-pipeline-bucket",  # optional, will query from training job
        "model_name": "house-price-model-v1",  # optional, will generate
        "version": "1.0.0"  # optional, defaults to timestamp-based
    }
    """
    
    try:
        # Get table name from environment or use default
        table_name = os.environ.get('MODEL_TABLE_NAME', 'ml-pipeline-models')
        table = dynamodb.Table(table_name)
        
        # Parse event
        training_job_name = event.get('training_job_name')
        if not training_job_name:
            raise ValueError("Missing 'training_job_name' in event")
        
        logger.info(f"Registering model for training job: {training_job_name}")
        
        # Get training job details from SageMaker
        training_job = sagemaker_client.describe_training_job(
            TrainingJobName=training_job_name
        )
        
        # Extract model artifact S3 URI
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
        model_name = event.get('model_name', f"house-price-model-{datetime.now().strftime('%Y%m%d')}")
        model_version = event.get('version', datetime.now().strftime('%Y%m%d-%H%M%S'))
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
        
        return {
            'statusCode': 200,
            'model_id': model_id,
            'model_name': model_name,
            'version': model_version,
            'model_artifact_s3_uri': model_artifact_s3_uri,
            'train_rmse': train_rmse,
            'dynamodb_table': table_name
        }
        
    except Exception as e:
        logger.error(f"Error registering model: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'statusCode': 500,
            'error': str(e)
        }

