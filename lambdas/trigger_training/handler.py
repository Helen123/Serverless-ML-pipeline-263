"""
Lambda Handler to Trigger SageMaker Training Job
Called by Step Functions or EventBridge when feature data is ready
"""

import json
import boto3
import logging
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sagemaker_client = boto3.client('sagemaker')
s3_client = boto3.client('s3')

def lambda_handler(event, context):
    """
    Trigger SageMaker training job
    
    Expected event:
    {
        "bucket": "ml-pipeline-bucket",
        "feature_key": "feature_store/data_features.csv",
        "training_config": {...}  # optional
    }
    """
    
    try:
        # Parse event
        bucket = event.get('bucket', 'ml-pipeline-dev-954976298878-us-west-2-helen')
        feature_key = event.get('feature_key')
        
        if not feature_key:
            # Try to get from EventBridge S3 event
            if 'detail' in event and 'object' in event['detail']:
                feature_key = event['detail']['object']['key']
                bucket = event['detail']['bucket']['name']
            else:
                raise ValueError("Missing 'feature_key' in event")
        
        logger.info(f"Triggering training job for: s3://{bucket}/{feature_key}")
        
        # Training job configuration
        training_config = event.get('training_config', {})
        job_name = training_config.get(
            'job_name',
            f"house-price-training-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        
        # SageMaker role (should be passed or in environment)
        role_arn = training_config.get(
            'role_arn',
            event.get('role_arn', 'arn:aws:iam::954976298878:role/SageMakerExecutionRole')
        )
        
        # Container image - use correct XGBoost image for us-west-2
        # AWS provides XGBoost containers in each region with region-specific account IDs
        # For us-west-2: 246618743249.dkr.ecr.us-west-2.amazonaws.com/sagemaker-xgboost:1.5-1
        # Alternative: Use the retrieve() helper or check AWS docs for latest
        image_uri = training_config.get(
            'image_uri',
            '246618743249.dkr.ecr.us-west-2.amazonaws.com/sagemaker-xgboost:1.5-1'  # us-west-2 XGBoost container
        )
        
        # Hyperparameters
        hyperparameters = training_config.get('hyperparameters', {
            'max_depth': '6',
            'eta': '0.3',
            'min_child_weight': '1',
            'subsample': '0.8',
            'colsample_bytree': '0.8',
            'num_round': '100',
            'objective': 'reg:squarederror',
            'eval_metric': 'rmse'
        })
        
        # Training input - correct format for SageMaker API
        # XGBoost built-in container expects channel name 'train' (not 'training')
        training_input = {
            'ChannelName': 'train',  # Built-in XGBoost expects 'train' channel
            'ContentType': 'text/csv',
            'DataSource': {
                'S3DataSource': {
                    'S3DataType': 'S3Prefix',
                    'S3Uri': f's3://{bucket}/{feature_key}',
                    'S3DataDistributionType': 'FullyReplicated'
                }
            }
        }
        
        # Create training job
        training_job_params = {
            'TrainingJobName': job_name,
            'RoleArn': role_arn,
            'AlgorithmSpecification': {
                'TrainingImage': image_uri,
                'TrainingInputMode': 'File'
            },
            'InputDataConfig': [training_input],
            'OutputDataConfig': {
                'S3OutputPath': f's3://{bucket}/models/'
            },
            'ResourceConfig': {
                'InstanceType': 'ml.m5.large',  # Using m5.large (quota increase requested)
                'InstanceCount': 1,
                'VolumeSizeInGB': 30
            },
            'StoppingCondition': {
                'MaxRuntimeInSeconds': 3600  # 1 hour max
            },
            'HyperParameters': hyperparameters
        }
        
        # Add VPC config if needed (optional)
        if 'vpc_config' in training_config:
            training_job_params['VpcConfig'] = training_config['vpc_config']
        
        logger.info(f"Creating training job: {job_name}")
        response = sagemaker_client.create_training_job(**training_job_params)
        
        logger.info(f"Training job created: {response['TrainingJobArn']}")
        
        return {
            'statusCode': 200,
            'training_job_name': job_name,
            'training_job_arn': response['TrainingJobArn'],
            'status': 'InProgress'
        }
        
    except Exception as e:
        logger.error(f"Error creating training job: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'statusCode': 500,
            'error': str(e)
        }

