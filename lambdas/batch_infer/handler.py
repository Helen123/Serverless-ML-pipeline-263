"""
Lambda Handler for Batch Inference
Triggers SageMaker Batch Transform job when data is uploaded to S3 to_infer/
"""

import json
import os
import boto3
import logging
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sagemaker_client = boto3.client('sagemaker')
s3_client = boto3.client('s3')

def lambda_handler(event, context):
    """
    Trigger SageMaker Batch Transform job for batch inference
    
    Expected event (EventBridge S3 format):
    {
        "detail": {
            "bucket": {"name": "ml-pipeline-bucket"},
            "object": {"key": "to_infer/new_houses_20251108.csv"}
        }
    }
    OR direct invocation:
    {
        "bucket": "ml-pipeline-bucket",
        "input_key": "to_infer/new_houses_20251108.csv",
        "model_name": "house-price-model-20251108-032257",  # optional
        "endpoint_name": "house-price-endpoint"  # alternative: use endpoint instead of model
    }
    """
    
    try:
        # Parse event
        bucket = None
        input_key = None
        
        if 'detail' in event:
            # EventBridge S3 event
            bucket = event['detail']['bucket']['name']
            input_key = event['detail']['object']['key']
        else:
            # Direct invocation
            bucket = event.get('bucket', os.environ.get('BUCKET', 'ml-pipeline-dev-954976298878-us-west-2-helen'))
            input_key = event.get('input_key')
            if not input_key:
                raise ValueError("Missing 'input_key' in event")
        
        # Only process files in to_infer/ prefix
        if not input_key.startswith('to_infer/'):
            logger.info(f"Skipping file not in to_infer/ prefix: {input_key}")
            return {
                'statusCode': 200,
                'message': f"Skipped: {input_key} (not in to_infer/)"
            }
        
        logger.info(f"Processing batch inference for: s3://{bucket}/{input_key}")
        
        # Get model name or endpoint name
        model_name = event.get('model_name')
        endpoint_name = event.get('endpoint_name')
        
        if not model_name and not endpoint_name:
            # Try to get from DynamoDB (latest deployed model)
            model_name = get_latest_model_name()
            if not model_name:
                raise ValueError("No model_name or endpoint_name provided, and no model found in DynamoDB")
        
        # Generate output path
        output_prefix = event.get('output_prefix', 'predicted/')
        # Extract filename without extension
        input_filename = input_key.split('/')[-1]
        output_key = f"{output_prefix}{input_filename.replace('.csv', '_predictions.csv')}"
        
        # Generate batch transform job name
        job_name = event.get('job_name', f"batch-infer-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        
        # SageMaker execution role
        role_arn = event.get('role_arn', 'arn:aws:iam::954976298878:role/SageMakerExecutionRole')
        
        # Container image (same as training)
        image_uri = event.get('image_uri', '246618743249.dkr.ecr.us-west-2.amazonaws.com/sagemaker-xgboost:1.5-1')
        
        if endpoint_name:
            # Use endpoint for batch inference (alternative approach)
            logger.info(f"Using endpoint for batch inference: {endpoint_name}")
            # Note: For large batch jobs, Batch Transform is more efficient
            # But we can also use the endpoint with async inference
            return invoke_batch_via_endpoint(endpoint_name, bucket, input_key, output_key)
        else:
            # Use Batch Transform (recommended for large batches)
            logger.info(f"Creating Batch Transform job: {job_name}")
            
            transform_job_params = {
                'TransformJobName': job_name,
                'ModelName': model_name,
                'MaxConcurrentTransforms': 1,
                'MaxPayloadInMB': 6,
                'BatchStrategy': 'MultiRecord',  # Process multiple records per request
                'TransformInput': {
                    'DataSource': {
                        'S3DataSource': {
                            'S3DataType': 'S3Prefix',
                            'S3Uri': f's3://{bucket}/{input_key}'
                        }
                    },
                    'ContentType': 'text/csv'
                },
                'TransformOutput': {
                    'S3OutputPath': f's3://{bucket}/{output_prefix}',
                    'AssembleWith': 'Line'
                },
                'TransformResources': {
                    'InstanceType': 'ml.m5.large',
                    'InstanceCount': 1
                }
            }
            
            response = sagemaker_client.create_transform_job(**transform_job_params)
            
            logger.info(f"Batch Transform job created: {response['TransformJobArn']}")
            
            return {
                'statusCode': 200,
                'transform_job_name': job_name,
                'transform_job_arn': response['TransformJobArn'],
                'input_s3_uri': f's3://{bucket}/{input_key}',
                'output_s3_uri': f's3://{bucket}/{output_prefix}',
                'status': 'InProgress'
            }
        
    except Exception as e:
        logger.error(f"Error creating batch transform job: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'statusCode': 500,
            'error': str(e)
        }

def get_latest_model_name():
    """Get the latest deployed model name from DynamoDB"""
    try:
        import boto3
        dynamodb = boto3.resource('dynamodb')
        table_name = os.environ.get('MODEL_TABLE_NAME', 'ml-pipeline-models')
        table = dynamodb.Table(table_name)
        
        # Scan for deployed models (this is a simple approach)
        # In production, you might want to use GSI or query by is_deployed
        response = table.scan(
            FilterExpression='is_deployed = :deployed',
            ExpressionAttributeValues={':deployed': True}
        )
        
        if response['Items']:
            # Sort by created_timestamp descending and get the latest
            items = sorted(response['Items'], key=lambda x: x.get('created_timestamp', 0), reverse=True)
            if items:
                # Extract model name from model_artifact_s3_uri or use training_job_name
                # For SageMaker, we need the model name, not the training job name
                # We'll need to construct it or store it in DynamoDB
                # For now, return None and require explicit model_name
                return None
        
        return None
    except Exception as e:
        logger.warning(f"Could not fetch model from DynamoDB: {str(e)}")
        return None

def invoke_batch_via_endpoint(endpoint_name, bucket, input_key, output_key):
    """
    Alternative: Use endpoint for batch inference (for smaller batches)
    This reads from S3, invokes endpoint for each record, and writes results back
    """
    import boto3
    import csv
    import io
    
    sagemaker_runtime = boto3.client('sagemaker-runtime')
    
    # Read input data from S3
    response = s3_client.get_object(Bucket=bucket, Key=input_key)
    input_data = response['Body'].read().decode('utf-8')
    
    # Parse CSV and invoke endpoint for each row
    reader = csv.DictReader(io.StringIO(input_data))
    predictions = []
    
    for row in reader:
        # Convert row to features (similar to online_infer)
        # This is simplified - you'd need proper feature engineering
        features_csv = ','.join([str(row.get(col, 0)) for col in reader.fieldnames])
        
        # Invoke endpoint
        response = sagemaker_runtime.invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType='text/csv',
            Body=features_csv.encode('utf-8')
        )
        
        prediction = float(response['Body'].read().decode('utf-8').strip())
        row['predicted_price'] = prediction
        predictions.append(row)
    
    # Write predictions to S3
    output_csv = io.StringIO()
    if predictions:
        writer = csv.DictWriter(output_csv, fieldnames=list(predictions[0].keys()))
        writer.writeheader()
        writer.writerows(predictions)
    
    s3_client.put_object(
        Bucket=bucket,
        Key=output_key,
        Body=output_csv.getvalue().encode('utf-8'),
        ContentType='text/csv'
    )
    
    return {
        'statusCode': 200,
        'message': f'Batch inference completed via endpoint',
        'output_s3_uri': f's3://{bucket}/{output_key}',
        'records_processed': len(predictions)
    }

