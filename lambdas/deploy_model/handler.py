"""
Lambda Handler to Deploy Model to SageMaker Endpoint
Creates SageMaker model, endpoint configuration, and endpoint
"""

import json
import os
import boto3
import logging
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sagemaker_client = boto3.client('sagemaker')
dynamodb = boto3.resource('dynamodb')

def lambda_handler(event, context):
    """
    Deploy model to SageMaker endpoint
    
    Expected event:
    {
        "model_id": "house-price-model-20251108-032257",  # from DynamoDB
        "endpoint_name": "house-price-endpoint",  # optional, will generate
        "instance_type": "ml.m5.large",  # optional, defaults to ml.m5.large
        "initial_instance_count": 1,  # optional, defaults to 1
        "use_serverless": false  # optional, use serverless inference instead
    }
    OR
    {
        "model_artifact_s3_uri": "s3://bucket/models/.../model.tar.gz",
        "image_uri": "246618743249.dkr.ecr.us-west-2.amazonaws.com/sagemaker-xgboost:1.5-1",
        "endpoint_name": "house-price-endpoint"
    }
    """
    
    try:
        # Get table name from environment or use default
        table_name = os.environ.get('MODEL_TABLE_NAME', 'ml-pipeline-models')
        table = dynamodb.Table(table_name)
        
        # Parse event
        model_id = event.get('model_id')
        model_artifact_s3_uri = event.get('model_artifact_s3_uri')
        image_uri = event.get('image_uri')
        
        # If model_id provided, fetch from DynamoDB
        if model_id:
            logger.info(f"Fetching model metadata for: {model_id}")
            response = table.get_item(Key={'model_id': model_id})
            
            if 'Item' not in response:
                raise ValueError(f"Model not found in DynamoDB: {model_id}")
            
            model_item = response['Item']
            model_artifact_s3_uri = model_item.get('model_artifact_s3_uri')
            if not model_artifact_s3_uri:
                raise ValueError(f"No model artifact URI found for model: {model_id}")
            
            # Use XGBoost image URI (same as training)
            image_uri = image_uri or '246618743249.dkr.ecr.us-west-2.amazonaws.com/sagemaker-xgboost:1.5-1'
        else:
            # Direct deployment without DynamoDB lookup
            if not model_artifact_s3_uri or not image_uri:
                raise ValueError("Either 'model_id' or both 'model_artifact_s3_uri' and 'image_uri' must be provided")
        
        # Generate endpoint name if not provided
        endpoint_name = event.get('endpoint_name', f"house-price-endpoint-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        
        # Check if endpoint already exists
        endpoint_exists = False
        try:
            existing_endpoint = sagemaker_client.describe_endpoint(EndpointName=endpoint_name)
            endpoint_exists = True
            logger.info(f"Endpoint already exists: {endpoint_name}")
        except sagemaker_client.exceptions.ClientError as e:
            if e.response['Error']['Code'] == 'ValidationException':
                endpoint_exists = False
            else:
                raise
        
        # SageMaker execution role
        role_arn = event.get('role_arn', 'arn:aws:iam::954976298878:role/SageMakerExecutionRole')
        
        # Generate model name
        model_name = event.get('model_name', f"house-price-model-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        
        # Create SageMaker model
        logger.info(f"Creating SageMaker model: {model_name}")
        create_model_response = sagemaker_client.create_model(
            ModelName=model_name,
            PrimaryContainer={
                'Image': image_uri,
                'ModelDataUrl': model_artifact_s3_uri
            },
            ExecutionRoleArn=role_arn
        )
        logger.info(f"Model created: {create_model_response['ModelArn']}")
        
        # Determine deployment type (default to serverless for cost efficiency)
        use_serverless = event.get('use_serverless', True)
        
        if use_serverless:
            # Serverless inference configuration
            serverless_config = event.get('serverless_config', {
                'MemorySizeInMB': 2048,
                'MaxConcurrency': 5
            })
            
            # Create endpoint configuration with serverless config
            endpoint_config_name = event.get('endpoint_config_name', f"{endpoint_name}-config-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
            logger.info(f"Creating serverless endpoint configuration: {endpoint_config_name}")
            
            sagemaker_client.create_endpoint_config(
                EndpointConfigName=endpoint_config_name,
                ProductionVariants=[{
                    'VariantName': 'AllTraffic',
                    'ModelName': model_name,
                    'ServerlessConfig': serverless_config
                }]
            )
        else:
            # Real-time inference configuration
            instance_type = event.get('instance_type', 'ml.m5.large')
            initial_instance_count = event.get('initial_instance_count', 1)
            
            endpoint_config_name = event.get('endpoint_config_name', f"{endpoint_name}-config-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
            logger.info(f"Creating endpoint configuration: {endpoint_config_name}")
            
            sagemaker_client.create_endpoint_config(
                EndpointConfigName=endpoint_config_name,
                ProductionVariants=[{
                    'VariantName': 'AllTraffic',
                    'ModelName': model_name,
                    'InstanceType': instance_type,
                    'InitialInstanceCount': initial_instance_count
                }]
            )
        
        logger.info(f"Endpoint configuration created: {endpoint_config_name}")
        
        # Create or update endpoint
        if endpoint_exists:
            logger.info(f"Updating existing endpoint: {endpoint_name}")
            sagemaker_client.update_endpoint(
                EndpointName=endpoint_name,
                EndpointConfigName=endpoint_config_name
            )
            endpoint_status = 'Updating'
        else:
            logger.info(f"Creating new endpoint: {endpoint_name}")
            create_endpoint_response = sagemaker_client.create_endpoint(
                EndpointName=endpoint_name,
                EndpointConfigName=endpoint_config_name
            )
            endpoint_status = 'Creating'
        
        # Update DynamoDB if model_id was provided
        if model_id:
            logger.info(f"Updating DynamoDB with deployment info for: {model_id}")
            table.update_item(
                Key={'model_id': model_id},
                UpdateExpression='SET is_deployed = :deployed, endpoint_name = :endpoint, deployment_timestamp = :timestamp',
                ExpressionAttributeValues={
                    ':deployed': True,
                    ':endpoint': endpoint_name,
                    ':timestamp': datetime.utcnow().isoformat()
                }
            )
        
        return {
            'statusCode': 200,
            'model_name': model_name,
            'model_arn': create_model_response['ModelArn'],
            'endpoint_name': endpoint_name,
            'endpoint_config_name': endpoint_config_name,
            'endpoint_status': endpoint_status,
            'use_serverless': use_serverless,
            'message': f"Endpoint {'updating' if endpoint_exists else 'creating'}. Use describe_endpoint to check status."
        }
        
    except Exception as e:
        logger.error(f"Error deploying model: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'statusCode': 500,
            'error': str(e)
        }

