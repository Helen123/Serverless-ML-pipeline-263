"""
XGBoost Training Script for House Price Prediction
Runs in SageMaker training container
"""

import argparse
import os
import pandas as pd
import numpy as np
import xgboost as xgb
import joblib
import json
import tarfile
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error, r2_score, mean_absolute_error

def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser()
    
    # SageMaker environment variables
    parser.add_argument('--model-dir', type=str, default=os.environ.get('SM_MODEL_DIR', '/opt/ml/model'))
    parser.add_argument('--train', type=str, default=os.environ.get('SM_CHANNEL_TRAINING', '/opt/ml/input/data/training'))
    parser.add_argument('--validation', type=str, default=os.environ.get('SM_CHANNEL_VALIDATION', '/opt/ml/input/data/validation'))
    parser.add_argument('--output-data-dir', type=str, default=os.environ.get('SM_OUTPUT_DATA_DIR', '/opt/ml/output'))
    
    # Hyperparameters
    parser.add_argument('--max-depth', type=int, default=6)
    parser.add_argument('--eta', type=float, default=0.3)
    parser.add_argument('--min-child-weight', type=int, default=1)
    parser.add_argument('--subsample', type=float, default=0.8)
    parser.add_argument('--colsample-bytree', type=float, default=0.8)
    parser.add_argument('--num-round', type=int, default=100)
    parser.add_argument('--objective', type=str, default='reg:squarederror')
    parser.add_argument('--eval-metric', type=str, default='rmse')
    
    return parser.parse_args()

def load_data(data_path):
    """Load training/validation data from CSV"""
    files = [f for f in os.listdir(data_path) if f.endswith('.csv')]
    if not files:
        raise ValueError(f"No CSV files found in {data_path}")
    
    # Load first CSV file found
    csv_path = os.path.join(data_path, files[0])
    df = pd.read_csv(csv_path)
    
    print(f"Loaded data from {csv_path}: {df.shape}")
    return df

def prepare_features(df):
    """Prepare features and target for training"""
    # Identify target column (price or MedHouseVal)
    target_col = None
    if 'price' in df.columns:
        target_col = 'price'
    elif 'MedHouseVal' in df.columns:
        target_col = 'MedHouseVal'
    else:
        raise ValueError("No target column found. Expected 'price' or 'MedHouseVal'")
    
    # Separate features and target
    # Drop non-feature columns
    exclude_cols = [target_col, 'Unnamed: 0', 'index']
    feature_cols = [col for col in df.columns if col not in exclude_cols]
    
    X = df[feature_cols].copy()
    y = df[target_col].copy()
    
    # Handle any remaining NaN values
    X = X.fillna(0)
    
    print(f"Features shape: {X.shape}, Target shape: {y.shape}")
    print(f"Feature columns: {list(X.columns)}")
    
    return X, y, feature_cols

def train_model(X_train, y_train, X_val, y_val, args):
    """Train XGBoost model"""
    print("Starting XGBoost training...")
    print(f"Training samples: {len(X_train)}, Validation samples: {len(X_val)}")
    
    # Create DMatrix for XGBoost
    dtrain = xgb.DMatrix(X_train, label=y_train)
    dval = xgb.DMatrix(X_val, label=y_val)
    
    # Set hyperparameters
    params = {
        'max_depth': args.max_depth,
        'eta': args.eta,
        'min_child_weight': args.min_child_weight,
        'subsample': args.subsample,
        'colsample_bytree': args.colsample_bytree,
        'objective': args.objective,
        'eval_metric': args.eval_metric,
        'verbosity': 1
    }
    
    print(f"Hyperparameters: {params}")
    
    # Train model
    evals = [(dtrain, 'train'), (dval, 'validation')]
    model = xgb.train(
        params=params,
        dtrain=dtrain,
        num_boost_round=args.num_round,
        evals=evals,
        early_stopping_rounds=10,
        verbose_eval=10
    )
    
    return model

def evaluate_model(model, X, y, feature_cols):
    """Evaluate model and return metrics"""
    dtest = xgb.DMatrix(X)
    y_pred = model.predict(dtest)
    
    rmse = np.sqrt(mean_squared_error(y, y_pred))
    mae = mean_absolute_error(y, y_pred)
    r2 = r2_score(y, y_pred)
    
    metrics = {
        'rmse': float(rmse),
        'mae': float(mae),
        'r2': float(r2),
        'mean_actual': float(y.mean()),
        'mean_predicted': float(y_pred.mean())
    }
    
    print(f"Metrics: RMSE={rmse:.4f}, MAE={mae:.4f}, R²={r2:.4f}")
    
    return metrics

if __name__ == '__main__':
    args = parse_args()
    
    print("=" * 50)
    print("XGBoost Training Job")
    print("=" * 50)
    
    # Load training data
    print("\nLoading training data...")
    train_df = load_data(args.train)
    X_train, y_train, feature_cols = prepare_features(train_df)
    
    # Load validation data if available, otherwise split training data
    if os.path.exists(args.validation) and os.listdir(args.validation):
        print("\nLoading validation data...")
        val_df = load_data(args.validation)
        X_val, y_val, _ = prepare_features(val_df)
    else:
        print("\nSplitting training data for validation...")
        X_train, X_val, y_train, y_val = train_test_split(
            X_train, y_train, test_size=0.2, random_state=42
        )
    
    # Train model
    print("\nTraining model...")
    model = train_model(X_train, y_train, X_val, y_val, args)
    
    # Evaluate on validation set
    print("\nEvaluating model...")
    metrics = evaluate_model(model, X_val, y_val, feature_cols)
    
    # Save model
    print(f"\nSaving model to {args.model_dir}...")
    model_path = os.path.join(args.model_dir, 'xgboost-model')
    model.save_model(model_path)
    
    # Save feature names for inference
    feature_info = {
        'feature_names': feature_cols,
        'target_name': 'price' if 'price' in train_df.columns else 'MedHouseVal'
    }
    with open(os.path.join(args.model_dir, 'feature_info.json'), 'w') as f:
        json.dump(feature_info, f)
    
    # Save metrics
    metrics_path = os.path.join(args.output_data_dir, 'metrics.json')
    os.makedirs(args.output_data_dir, exist_ok=True)
    with open(metrics_path, 'w') as f:
        json.dump(metrics, f, indent=2)
    
    print(f"\nModel saved to {model_path}")
    print(f"Metrics saved to {metrics_path}")
    print("Training completed successfully!")

