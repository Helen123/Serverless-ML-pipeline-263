"""
Lambda Handler for Online Inference via API Gateway
Receives prediction requests and calls SageMaker endpoint
"""

import json
import os
import boto3
import logging
import csv
import io

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sagemaker_runtime = boto3.client('sagemaker-runtime')
dynamodb = boto3.resource('dynamodb')

def lambda_handler(event, context):
    """
    Handle online inference requests from API Gateway
    
    Expected event (API Gateway format):
    {
        "httpMethod": "POST",
        "body": "{\"area_sqm\": 150.5, \"latitude\": 37.7749, \"longitude\": -122.4194, ...}"
    }
    OR direct invocation:
    {
        "area_sqm": 150.5,
        "latitude": 37.7749,
        "longitude": -122.4194,
        ...
    }
    """
    
    try:
        # Get endpoint name from environment or use default
        endpoint_name = os.environ.get('ENDPOINT_NAME', 'house-price-endpoint')
        
        # Parse request body
        if 'body' in event:
            # API Gateway format
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            # Direct invocation
            body = event
        
        # Extract features from request
        # Expected features (after feature engineering): area_sqm, latitude, longitude, house_age, med_inc, etc.
        # We need to apply the same feature engineering as in feature_build Lambda
        features = extract_and_engineer_features(body)
        
        # Convert to CSV format (SageMaker XGBoost expects CSV)
        csv_data = features_to_csv(features)
        
        logger.info(f"Invoking endpoint: {endpoint_name} with features: {features}")
        
        # Invoke SageMaker endpoint
        response = sagemaker_runtime.invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType='text/csv',
            Body=csv_data.encode('utf-8')
        )
        
        # Parse prediction result
        prediction_result = response['Body'].read().decode('utf-8')
        # XGBoost returns a single float value
        predicted_price = float(prediction_result.strip())
        
        logger.info(f"Prediction: ${predicted_price:,.2f}")
        
        # Format response for API Gateway
        response_body = {
            'predicted_price': predicted_price,
            'predicted_price_formatted': f"${predicted_price:,.2f}",
            'features': features
        }
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'  # CORS for web frontend
            },
            'body': json.dumps(response_body)
        }
        
    except Exception as e:
        logger.error(f"Error during inference: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': str(e),
                'message': 'Failed to generate prediction'
            })
        }

def extract_and_engineer_features(request_data):
    """
    Extract and engineer features from request data
    This should match the feature engineering in feature_build Lambda
    """
    import math
    
    features = {}
    
    # Extract base features
    features['area_sqm'] = float(request_data.get('area_sqm', request_data.get('area_sqft', 0)) * 0.092903)  # Convert sqft to sqm if needed
    features['latitude'] = float(request_data.get('latitude', 0))
    features['longitude'] = float(request_data.get('longitude', 0))
    features['house_age'] = float(request_data.get('house_age', request_data.get('HouseAge', 0)))
    features['med_inc'] = float(request_data.get('med_inc', request_data.get('MedInc', 0)))
    features['ave_rooms'] = float(request_data.get('ave_rooms', request_data.get('AveRooms', 0)))
    features['ave_bedrms'] = float(request_data.get('ave_bedrms', request_data.get('AveBedrms', 0)))
    features['population'] = float(request_data.get('population', request_data.get('Population', 0)))
    features['ave_occup'] = float(request_data.get('ave_occup', request_data.get('AveOccup', 0)))
    
    # Feature engineering (matching feature_build Lambda)
    # Distance to center (San Francisco: 37.7749, -122.4194)
    center_lat, center_lon = 37.7749, -122.4194
    features['distance_to_center_km'] = haversine_distance(
        features['latitude'], features['longitude'],
        center_lat, center_lon
    )
    
    # Log transforms
    features['log_area_sqm'] = math.log1p(features['area_sqm'])
    # Note: We don't have price in the request, so we skip log_price
    
    # Squared features
    features['area_sqm_squared'] = features['area_sqm'] ** 2
    features['med_inc_squared'] = features['med_inc'] ** 2
    
    # Interaction features (skip price-dependent ones for inference)
    # price_per_sqm and price_per_age require price, so we skip them
    
    # Room ratio
    if features['ave_bedrms'] > 0:
        features['rooms_per_bedroom'] = features['ave_rooms'] / features['ave_bedrms']
    else:
        features['rooms_per_bedroom'] = 0
    
    # Population density
    if features['ave_occup'] > 0:
        features['population_density'] = features['population'] / features['ave_occup']
    else:
        features['population_density'] = 0
    
    # One-hot encoding for house_type (if provided)
    house_type = request_data.get('house_type', '').lower()
    features['house_type_apartment'] = 1 if 'apartment' in house_type or 'condo' in house_type else 0
    features['house_type_house'] = 1 if 'house' in house_type or 'single' in house_type else 0
    features['house_type_townhouse'] = 1 if 'townhouse' in house_type or 'town' in house_type else 0
    
    return features

def haversine_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two points in kilometers"""
    import math
    
    R = 6371  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.asin(math.sqrt(a))
    return R * c

def features_to_csv(features):
    """
    Convert features dictionary to CSV format expected by SageMaker XGBoost
    Note: During training, target (price) is first column, but during inference,
    we only provide features in the same order (excluding target).
    
    Column order must match feature_build Lambda output (excluding target):
    All features in the order they appear after the target column
    """
    # Define column order (must match feature_build Lambda output, excluding target)
    # Based on feature_build logic, the order after target removal is:
    # Original columns (excluding target) + engineered features in order of creation
    
    # Base columns (from cleaned data, excluding target)
    base_columns = [
        'area_sqm', 'latitude', 'longitude', 'house_age', 'med_inc',
        'ave_rooms', 'ave_bedrms', 'population', 'ave_occup'
    ]
    
    # Engineered features (in order of creation from feature_build)
    engineered_columns = [
        'distance_to_center_km',  # Added first
        'area_sqm_squared',       # Added second
        'log_area_sqm',           # Added third
        'log_price',              # Added if price exists (we skip for inference)
        'med_inc_squared',        # Added later
        'price_per_sqm',         # Interaction feature (requires price, skip for inference)
        'price_per_age',          # Interaction feature (requires price, skip for inference)
        'rooms_per_bedroom',      # Room ratio
        'population_density'      # Population ratio
    ]
    
    # One-hot encoded columns (dynamic, depends on house_type values in training data)
    # We'll include common ones, but this should match training data
    one_hot_columns = [
        'house_type_apartment', 'house_type_house', 'house_type_townhouse'
    ]
    
    # Final column order (excluding target and price-dependent features)
    columns = base_columns + [
        'distance_to_center_km',
        'area_sqm_squared',
        'log_area_sqm',
        'med_inc_squared',
        'rooms_per_bedroom',
        'population_density'
    ] + one_hot_columns
    
    # Create CSV row with values (use 0 for missing features)
    values = []
    for col in columns:
        val = features.get(col, 0)
        # Handle NaN or None
        if val is None or (isinstance(val, float) and (val != val)):  # NaN check
            val = 0
        values.append(str(val))
    
    csv_row = ','.join(values)
    
    return csv_row

