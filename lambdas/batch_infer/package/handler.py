"""
Lambda Handler for Batch Inference
Triggers SageMaker Batch Transform job when data is uploaded to S3 to_infer/
Applies feature engineering to raw CSV before batch transform
"""

import json
import os
import boto3
import logging
import pandas as pd
import numpy as np
from datetime import datetime
from io import BytesIO
from math import radians, sin, cos, sqrt, atan2

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
            # This will get the endpoint_name from DynamoDB, then query SageMaker for the actual model name
            model_name = get_latest_model_name()
            if not model_name:
                raise ValueError("No model_name or endpoint_name provided, and no model found in DynamoDB")
        
        # If endpoint_name is provided but not model_name, get model name from endpoint config
        if endpoint_name and not model_name:
            model_name = get_model_name_from_endpoint(endpoint_name)
            if not model_name:
                raise ValueError(f"Could not find model name for endpoint: {endpoint_name}")
        
        # Step 1: Apply feature engineering to the raw CSV
        logger.info("Step 1: Reading raw CSV and applying feature engineering...")
        feature_engineered_key = apply_feature_engineering(bucket, input_key)
        logger.info(f"Feature engineering complete. Processed file: s3://{bucket}/{feature_engineered_key}")
        
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
            return invoke_batch_via_endpoint(endpoint_name, bucket, feature_engineered_key, output_key)
        else:
            # Use Batch Transform (recommended for large batches)
            logger.info(f"Step 2: Creating Batch Transform job: {job_name}")
            
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
                            'S3Uri': f's3://{bucket}/{feature_engineered_key}'
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
                'input_s3_uri': f's3://{bucket}/{feature_engineered_key}',
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
    """
    Get the latest deployed model name from DynamoDB.
    Returns the actual SageMaker model name by:
    1. Getting endpoint_name from DynamoDB
    2. Querying SageMaker endpoint config to get the model name
    """
    try:
        import boto3
        dynamodb = boto3.resource('dynamodb')
        table_name = os.environ.get('MODEL_TABLE_NAME', 'ml-pipeline-models')
        table = dynamodb.Table(table_name)
        
        # Scan for deployed models
        response = table.scan(
            FilterExpression='is_deployed = :deployed',
            ExpressionAttributeValues={':deployed': True}
        )
        
        if response['Items']:
            # Sort by created_timestamp descending and get the latest
            # Note: boto3 resource converts DynamoDB types automatically, so we can access directly
            items = sorted(
                response['Items'], 
                key=lambda x: int(x.get('created_timestamp', 0)) if isinstance(x.get('created_timestamp'), (int, str)) else 0, 
                reverse=True
            )
            if items:
                latest_item = items[0]
                # Get endpoint_name from DynamoDB (boto3 resource format - direct access)
                endpoint_name = latest_item.get('endpoint_name')
                if endpoint_name:
                    # Query SageMaker to get the actual model name from endpoint config
                    logger.info(f"Found endpoint in DynamoDB: {endpoint_name}, querying SageMaker for model name...")
                    return get_model_name_from_endpoint(endpoint_name)
        
        return None
    except Exception as e:
        logger.warning(f"Could not fetch model from DynamoDB: {str(e)}")
        return None

def get_model_name_from_endpoint(endpoint_name):
    """
    Get the SageMaker model name from an endpoint configuration.
    This queries the endpoint config to find which model is deployed.
    """
    try:
        # Describe the endpoint to get its config name
        endpoint_response = sagemaker_client.describe_endpoint(EndpointName=endpoint_name)
        endpoint_config_name = endpoint_response['EndpointConfigName']
        
        # Describe the endpoint config to get the model name
        config_response = sagemaker_client.describe_endpoint_config(EndpointConfigName=endpoint_config_name)
        
        # Get model name from the first production variant
        if config_response.get('ProductionVariants'):
            model_name = config_response['ProductionVariants'][0]['ModelName']
            logger.info(f"Found model name from endpoint {endpoint_name}: {model_name}")
            return model_name
        
        return None
    except Exception as e:
        logger.error(f"Could not get model name from endpoint {endpoint_name}: {str(e)}")
        return None

def apply_feature_engineering(bucket, input_key):
    """
    Apply feature engineering to raw CSV file and save to temporary S3 location.
    Returns the S3 key of the feature-engineered file.
    """
    try:
        # Read raw CSV from S3
        logger.info(f"Reading raw CSV from s3://{bucket}/{input_key}")
        response = s3_client.get_object(Bucket=bucket, Key=input_key)
        df = pd.read_csv(BytesIO(response['Body'].read()))
        
        logger.info(f"Raw data shape: {df.shape}, columns: {list(df.columns)}")
        
        # Apply feature engineering (same logic as feature_build Lambda)
        df_features = build_features_for_inference(df)
        
        logger.info(f"Feature-engineered data shape: {df_features.shape}")
        
        # Save to temporary S3 location (in to_infer/ with _features suffix)
        input_filename = input_key.split('/')[-1]
        feature_key = f"to_infer/{input_filename.replace('.csv', '_features.csv')}"
        
        # Convert to CSV (without target column, in the order expected by model)
        csv_buffer = BytesIO()
        # Write CSV without header, just the feature values in the correct order
        # The model expects features in this order (from training, excluding target):
        feature_columns = [
            'med_inc', 'house_age', 'ave_rooms', 'ave_bedrms', 'population', 'ave_occup',
            'latitude', 'longitude', 'distance_to_center_km', 'log_price', 'price_per_age',
            'rooms_per_bedroom', 'population_density', 'med_inc_squared'
        ]
        
        # Select only the columns that exist and in the right order
        available_cols = [col for col in feature_columns if col in df_features.columns]
        df_output = df_features[available_cols]
        
        # For missing columns, add zeros (e.g., log_price, price_per_age for inference)
        for col in feature_columns:
            if col not in df_output.columns:
                df_output[col] = 0
        
        # Reorder to match training order
        df_output = df_output[feature_columns]
        
        # Write CSV without header (Batch Transform expects no header)
        df_output.to_csv(csv_buffer, index=False, header=False)
        csv_buffer.seek(0)
        
        # Upload to S3
        s3_client.put_object(
            Bucket=bucket,
            Key=feature_key,
            Body=csv_buffer.getvalue(),
            ContentType='text/csv'
        )
        
        logger.info(f"Feature-engineered CSV saved to: s3://{bucket}/{feature_key}")
        return feature_key
        
    except Exception as e:
        logger.error(f"Error applying feature engineering: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise

def build_features_for_inference(df):
    """
    Build features from raw data for inference (no target column).
    Same feature engineering as training, but without price-dependent features.
    """
    df_features = df.copy()
    
    # 1. Calculate distance to city center
    if 'latitude' in df_features.columns and 'longitude' in df_features.columns:
        city_center_lat = 37.7749  # San Francisco
        city_center_lon = -122.4194
        
        df_features['distance_to_center_km'] = df_features.apply(
            lambda row: haversine_distance(
                row['latitude'], row['longitude'],
                city_center_lat, city_center_lon
            ),
            axis=1
        )
    
    # 2. Log transform for area (if exists)
    if 'area_sqm' in df_features.columns:
        df_features['log_area_sqm'] = np.log1p(df_features['area_sqm'])
        df_features['area_sqm_squared'] = df_features['area_sqm'] ** 2
    
    # 3. One-hot encoding for house_type
    if 'house_type' in df_features.columns:
        house_type_dummies = pd.get_dummies(df_features['house_type'], prefix='house_type', dummy_na=False)
        df_features = pd.concat([df_features, house_type_dummies], axis=1)
        df_features = df_features.drop(columns=['house_type'])
    
    # 4. Interaction features (without price)
    if 'ave_rooms' in df_features.columns and 'ave_bedrms' in df_features.columns:
        df_features['rooms_per_bedroom'] = df_features['ave_rooms'] / (df_features['ave_bedrms'] + 1e-6)
    
    if 'population' in df_features.columns and 'ave_occup' in df_features.columns:
        df_features['population_density'] = df_features['population'] / (df_features['ave_occup'] + 1e-6)
    
    # 5. Polynomial features
    if 'med_inc' in df_features.columns:
        df_features['med_inc_squared'] = df_features['med_inc'] ** 2
    
    # Add placeholder columns for price-dependent features (will be 0 for inference)
    df_features['log_price'] = 0
    df_features['price_per_age'] = 0
    
    return df_features

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two points in kilometers using Haversine formula"""
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    R = 6371.0  # Earth radius in km
    return R * c

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

