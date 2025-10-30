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
    Lambda handler for cleaning and transforming California Housing data
    
    Expected event structure:
    {
        "bucket": "ml-pipeline",
        "key": "raw/data.csv",
        "output_prefix": "processed/"
    }
    """
    
    try:
        # Parse event
        bucket = event.get('bucket', 'ml-pipeline')
        input_key = event.get('key')
        output_prefix = event.get('output_prefix', 'processed/')
        
        if not input_key:
            raise ValueError("Missing 'key' in event")
        
        logger.info(f"Processing file: s3://{bucket}/{input_key}")
        
        # Read data from S3
        response = s3_client.get_object(Bucket=bucket, Key=input_key)
        df = pd.read_csv(BytesIO(response['Body'].read()))
        
        logger.info(f"Original data shape: {df.shape}")
        
        # Clean data
        df_cleaned = clean_california_housing(df)
        
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

def clean_california_housing(df):
    """
    Clean California Housing dataset
    
    Cleaning steps:
    1. Handle missing values
    2. Remove outliers (e.g., negative values for positive features)
    3. Cap extreme values
    4. Remove duplicate rows
    5. Validate data types
    """
    
    df_cleaned = df.copy()
    
    # Remove duplicates
    df_cleaned = df_cleaned.drop_duplicates()
    logger.info(f"After removing duplicates: {df_cleaned.shape}")
    
    # Handle missing values - for California Housing dataset, typically minimal
    # Drop rows with any missing values in critical columns
    if df_cleaned.isnull().any().any():
        initial_rows = len(df_cleaned)
        df_cleaned = df_cleaned.dropna()
        logger.info(f"Dropped {initial_rows - len(df_cleaned)} rows with missing values")
    
    # Remove negative values for features that should be positive
    positive_features = ['MedInc', 'HouseAge', 'AveRooms', 'AveBedrms', 'Population', 
                        'AveOccup', 'Latitude', 'Longitude']
    
    for col in positive_features:
        if col in df_cleaned.columns:
            # Keep only positive values
            initial_count = len(df_cleaned)
            df_cleaned = df_cleaned[df_cleaned[col] > 0]
            dropped = initial_count - len(df_cleaned)
            if dropped > 0:
                logger.info(f"Dropped {dropped} rows with negative {col}")
    
    # Cap extreme outliers using IQR method
    # MedInc - median income (reasonable cap)
    if 'MedInc' in df_cleaned.columns:
        q25 = df_cleaned['MedInc'].quantile(0.25)
        q75 = df_cleaned['MedInc'].quantile(0.75)
        iqr = q75 - q25
        lower_bound = q25 - 3 * iqr
        upper_bound = q75 + 3 * iqr
        df_cleaned = df_cleaned[
            (df_cleaned['MedInc'] >= lower_bound) & 
            (df_cleaned['MedInc'] <= upper_bound)
        ]
    
    # AveRooms - cap unreasonably high values
    if 'AveRooms' in df_cleaned.columns:
        df_cleaned = df_cleaned[df_cleaned['AveRooms'] <= 20]
    
    # AveBedrms - cap unreasonably high values
    if 'AveBedrms' in df_cleaned.columns:
        df_cleaned = df_cleaned[df_cleaned['AveBedrms'] <= 5]
    
    # Latitude and Longitude - California bounds
    if 'Latitude' in df_cleaned.columns:
        df_cleaned = df_cleaned[
            (df_cleaned['Latitude'] >= 32.5) & 
            (df_cleaned['Latitude'] <= 42)
        ]
    
    if 'Longitude' in df_cleaned.columns:
        df_cleaned = df_cleaned[
            (df_cleaned['Longitude'] >= -124.5) & 
            (df_cleaned['Longitude'] <= -114)
        ]
    
    # Reset index after cleaning
    df_cleaned = df_cleaned.reset_index(drop=True)
    
    return df_cleaned

