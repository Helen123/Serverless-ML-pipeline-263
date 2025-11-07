"""
Lambda Handler for Data Cleaning and Transformation
Processes California Housing dataset from S3, cleans data, and outputs to processed folder
"""

import json
import boto3
import pandas as pd
import numpy as np
from io import BytesIO
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

def lambda_handler(event, context):
    """
    Lambda handler for cleaning and transforming housing data
    
    Handles two event types:
    1. EventBridge S3 event (from S3 upload trigger)
    2. Manual invocation with explicit bucket/key
    """
    
    try:
        # Parse different event types
        if 'detail' in event and 'source' in event and event.get('source') == 'aws.s3':
            # EventBridge S3 event format
            detail = event['detail']
            bucket = detail['bucket']['name']
            input_key = detail['object']['key']
            logger.info(f"Received EventBridge S3 event for: s3://{bucket}/{input_key}")
        elif 'Records' in event and len(event['Records']) > 0:
            # S3 direct notification format
            s3_event = event['Records'][0]['s3']
            bucket = s3_event['bucket']['name']
            input_key = s3_event['object']['key']
            logger.info(f"Received S3 direct notification for: s3://{bucket}/{input_key}")
        else:
            # Manual invocation
            bucket = event.get('bucket', 'ml-pipeline-dev-954976298878-us-west-2-helen')
            input_key = event.get('key')
            if not input_key:
                logger.error(f"Event structure: {json.dumps(event, default=str)}")
                raise ValueError("Missing 'key' in event. Event must be EventBridge S3 event, S3 notification, or manual invocation with 'key' field.")
            logger.info(f"Manual invocation for: s3://{bucket}/{input_key}")
        
        # Only process files in raw/ prefix
        if not input_key.startswith('raw/'):
            logger.info(f"Skipping file not in raw/ prefix: {input_key}")
            return {
                'statusCode': 200,
                'message': f'Skipped: {input_key} (not in raw/ prefix)'
            }
        
        output_prefix = event.get('output_prefix', 'processed/')
        
        logger.info(f"Processing file: s3://{bucket}/{input_key}")
        
        # Read data from S3
        response = s3_client.get_object(Bucket=bucket, Key=input_key)
        df = pd.read_csv(BytesIO(response['Body'].read()))
        
        logger.info(f"Original data shape: {df.shape}")
        
        # Clean data (supports both California Housing and general housing data)
        df_cleaned = clean_housing_data(df)
        
        logger.info(f"Cleaned data shape: {df_cleaned.shape}")
        logger.info(f"Dropped rows: {df.shape[0] - df_cleaned.shape[0]}")
        
        # Save cleaned data back to S3
        output_key = output_prefix + input_key.split('/')[-1].replace('.csv', '_cleaned.csv')
        
        # Convert to CSV and upload
        csv_buffer = BytesIO()
        df_cleaned.to_csv(csv_buffer, index=False)
        csv_buffer.seek(0)
        
        s3_client.put_object(
            Bucket=bucket,
            Key=output_key,
            Body=csv_buffer.getvalue()
        )
        
        logger.info(f"Saved cleaned data to: s3://{bucket}/{output_key}")
        
        # Return metadata for next steps
        return {
            'statusCode': 200,
            'bucket': bucket,
            'cleaned_key': output_key,
            'original_shape': df.shape,
            'cleaned_shape': df_cleaned.shape,
            'dropped_rows': df.shape[0] - df_cleaned.shape[0],
            'missing_values': df_cleaned.isnull().sum().to_dict()
        }
        
    except Exception as e:
        logger.error(f"Error processing data: {str(e)}")
        return {
            'statusCode': 500,
            'error': str(e)
        }

def clean_housing_data(df):
    """
    Clean housing transaction data
    
    Cleaning steps:
    1. Standardize field names (case-insensitive matching)
    2. Remove duplicates
    3. Handle missing values - drop rows with missing critical fields
    4. Remove abnormal values (e.g., price = 0)
    5. Standardize units (sqft → sqm conversion)
    6. Validate data types
    """
    
    df_cleaned = df.copy()
    
    # Step 1: Standardize field names (case-insensitive, common variations)
    column_mapping = {}
    for col in df_cleaned.columns:
        col_lower = col.lower().strip()
        # Map common variations to standard names
        if 'price' in col_lower or 'value' in col_lower:
            column_mapping[col] = 'price'
        elif 'area' in col_lower or 'sqft' in col_lower or 'sq_ft' in col_lower:
            column_mapping[col] = 'area_sqft'
        elif 'age' in col_lower or 'year' in col_lower:
            column_mapping[col] = 'house_age'
        elif 'lat' in col_lower:
            column_mapping[col] = 'latitude'
        elif 'lon' in col_lower or 'lng' in col_lower:
            column_mapping[col] = 'longitude'
        elif 'type' in col_lower:
            column_mapping[col] = 'house_type'
    
    df_cleaned = df_cleaned.rename(columns=column_mapping)
    if column_mapping:
        logger.info(f"Standardized column names: {column_mapping}")
    
    # Step 2: Remove duplicates
    initial_rows = len(df_cleaned)
    df_cleaned = df_cleaned.drop_duplicates()
    logger.info(f"After removing duplicates: {df_cleaned.shape[0]} rows (dropped {initial_rows - len(df_cleaned)})")
    
    # Step 3: Handle missing values - drop rows with missing critical fields
    critical_fields = ['price']
    if 'area_sqft' in df_cleaned.columns:
        critical_fields.append('area_sqft')
    
    missing_before = len(df_cleaned)
    df_cleaned = df_cleaned.dropna(subset=critical_fields)
    logger.info(f"After removing missing critical fields: {df_cleaned.shape[0]} rows (dropped {missing_before - len(df_cleaned)})")
    
    # Step 4: Remove abnormal values (price = 0 or negative)
    if 'price' in df_cleaned.columns:
        abnormal_before = len(df_cleaned)
        df_cleaned = df_cleaned[df_cleaned['price'] > 0]
        logger.info(f"After removing price <= 0: {df_cleaned.shape[0]} rows (dropped {abnormal_before - len(df_cleaned)})")
    
    # Step 5: Standardize units - convert sqft to sqm if needed
    if 'area_sqft' in df_cleaned.columns:
        # Check if values look like sqft (typically > 100) or sqm (typically < 1000 for houses)
        # If max > 1000, assume it's sqft and convert to sqm
        max_area = df_cleaned['area_sqft'].max()
        if max_area > 1000:
            df_cleaned['area_sqm'] = df_cleaned['area_sqft'] * 0.092903  # sqft to sqm
            logger.info(f"Converted area from sqft to sqm (max was {max_area:.2f})")
        else:
            # Already in sqm, just rename
            df_cleaned['area_sqm'] = df_cleaned['area_sqft']
            logger.info("Area already appears to be in sqm")
        
        # Remove negative or zero area
        df_cleaned = df_cleaned[df_cleaned['area_sqm'] > 0]
    
    # Remove negative values for other positive features
    positive_features = ['house_age', 'latitude', 'longitude']
    for col in positive_features:
        if col in df_cleaned.columns:
            initial_count = len(df_cleaned)
            df_cleaned = df_cleaned[df_cleaned[col] > 0]
            dropped = initial_count - len(df_cleaned)
            if dropped > 0:
                logger.info(f"Dropped {dropped} rows with non-positive {col}")
    
    # Reset index after cleaning
    df_cleaned = df_cleaned.reset_index(drop=True)
    
    logger.info(f"Final cleaned data shape: {df_cleaned.shape}")
    return df_cleaned

