"""
Lambda Handler for Feature Engineering
Reads cleaned data from S3 processed/, performs feature engineering, outputs to feature_store/
"""

import json
import boto3
import pandas as pd
import numpy as np
from io import BytesIO
import logging
from math import radians, sin, cos, sqrt, atan2

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client('s3')

def lambda_handler(event, context):
    """
    Lambda handler for feature engineering
    
    Handles two event types:
    1. EventBridge S3 event (from processed/ upload trigger)
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
        
        # Only process files in processed/ prefix
        if not input_key.startswith('processed/'):
            logger.info(f"Skipping file not in processed/ prefix: {input_key}")
            return {
                'statusCode': 200,
                'message': f'Skipped: {input_key} (not in processed/ prefix)'
            }
        
        output_prefix = event.get('output_prefix', 'feature_store/')
        
        logger.info(f"Processing file: s3://{bucket}/{input_key}")
        
        # Read cleaned data from S3
        response = s3_client.get_object(Bucket=bucket, Key=input_key)
        df = pd.read_csv(BytesIO(response['Body'].read()))
        
        logger.info(f"Original cleaned data shape: {df.shape}")
        
        # Perform feature engineering
        df_features = build_features(df)
        
        logger.info(f"Feature-engineered data shape: {df_features.shape}")
        logger.info(f"Added {df_features.shape[1] - df.shape[1]} new features")
        
        # Save feature-engineered data back to S3
        output_key = output_prefix + input_key.split('/')[-1].replace('_cleaned.csv', '_features.csv').replace('.csv', '_features.csv')
        
        # Convert to CSV and upload
        csv_buffer = BytesIO()
        df_features.to_csv(csv_buffer, index=False)
        csv_buffer.seek(0)
        
        s3_client.put_object(
            Bucket=bucket,
            Key=output_key,
            Body=csv_buffer.getvalue()
        )
        
        logger.info(f"Saved feature-engineered data to: s3://{bucket}/{output_key}")
        
        # Return metadata for next steps
        return {
            'statusCode': 200,
            'bucket': bucket,
            'features_key': output_key,
            'original_shape': df.shape,
            'features_shape': df_features.shape,
            'new_features': list(set(df_features.columns) - set(df.columns))
        }
        
    except Exception as e:
        logger.error(f"Error processing data: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'statusCode': 500,
            'error': str(e)
        }

def build_features(df):
    """
    Build features from cleaned housing data
    
    Feature engineering steps:
    1. Calculate distance to city center from lat/lon
    2. Generate squared area or log(price) transformations
    3. One-hot encoding for categorical features (house_type)
    4. Create interaction features
    """
    
    df_features = df.copy()
    
    # 1. Calculate distance to city center (using San Francisco as reference for California data)
    # For general housing data, use median lat/lon as city center
    if 'latitude' in df_features.columns and 'longitude' in df_features.columns:
        # San Francisco coordinates (or calculate median as city center)
        if df_features['latitude'].median() > 35:  # Likely California data
            city_center_lat = 37.7749  # San Francisco
            city_center_lon = -122.4194
        else:
            # Use median as city center for other regions
            city_center_lat = df_features['latitude'].median()
            city_center_lon = df_features['longitude'].median()
        
        df_features['distance_to_center_km'] = df_features.apply(
            lambda row: haversine_distance(
                row['latitude'], row['longitude'],
                city_center_lat, city_center_lon
            ),
            axis=1
        )
        logger.info(f"Added distance_to_center_km feature (range: {df_features['distance_to_center_km'].min():.2f} - {df_features['distance_to_center_km'].max():.2f} km)")
    
    # 2. Generate squared area or log transformations
    if 'area_sqm' in df_features.columns:
        df_features['area_sqm_squared'] = df_features['area_sqm'] ** 2
        logger.info("Added area_sqm_squared feature")
    
    if 'area_sqft' in df_features.columns and 'area_sqm' not in df_features.columns:
        df_features['area_sqft_squared'] = df_features['area_sqft'] ** 2
        logger.info("Added area_sqft_squared feature")
    
    # Log transform for price (add small epsilon to avoid log(0))
    if 'price' in df_features.columns:
        df_features['log_price'] = np.log1p(df_features['price'])  # log1p(x) = log(1+x) to handle zeros
        logger.info("Added log_price feature")
    
    # Log transform for area
    if 'area_sqm' in df_features.columns:
        df_features['log_area_sqm'] = np.log1p(df_features['area_sqm'])
        logger.info("Added log_area_sqm feature")
    
    # 3. One-hot encoding for categorical features
    if 'house_type' in df_features.columns:
        # One-hot encode house_type
        house_type_dummies = pd.get_dummies(df_features['house_type'], prefix='house_type', dummy_na=False)
        df_features = pd.concat([df_features, house_type_dummies], axis=1)
        # Drop original categorical column
        df_features = df_features.drop(columns=['house_type'])
        logger.info(f"One-hot encoded house_type into {len(house_type_dummies.columns)} features: {list(house_type_dummies.columns)}")
    
    # 4. Create interaction features
    if 'price' in df_features.columns and 'area_sqm' in df_features.columns:
        df_features['price_per_sqm'] = df_features['price'] / (df_features['area_sqm'] + 1e-6)  # Avoid division by zero
        logger.info("Added price_per_sqm feature")
    
    if 'house_age' in df_features.columns and 'price' in df_features.columns:
        df_features['price_per_age'] = df_features['price'] / (df_features['house_age'] + 1)  # Avoid division by zero
        logger.info("Added price_per_age feature")
    
    # Room ratio features (for California Housing dataset)
    if 'ave_rooms' in df_features.columns and 'ave_bedrms' in df_features.columns:
        df_features['rooms_per_bedroom'] = df_features['ave_rooms'] / (df_features['ave_bedrms'] + 1e-6)
        logger.info("Added rooms_per_bedroom feature")
    
    if 'population' in df_features.columns and 'ave_occup' in df_features.columns:
        df_features['population_density'] = df_features['population'] / (df_features['ave_occup'] + 1e-6)
        logger.info("Added population_density feature")
    
    # 5. Polynomial features for key numerical features
    if 'med_inc' in df_features.columns:
        df_features['med_inc_squared'] = df_features['med_inc'] ** 2
        logger.info("Added med_inc_squared feature")
    
    # Reset index
    df_features = df_features.reset_index(drop=True)
    
    logger.info(f"Final feature-engineered data shape: {df_features.shape}")
    logger.info(f"Feature columns: {list(df_features.columns)}")
    
    return df_features

def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points on Earth (in km)
    using the Haversine formula
    """
    # Convert latitude and longitude from degrees to radians
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    
    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    
    # Radius of Earth in kilometers
    R = 6371.0
    
    return R * c

