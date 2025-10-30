"""
Generate sample California Housing dataset for testing
Downloads and saves data from sklearn.datasets.fetch_california_housing
"""

from sklearn.datasets import fetch_california_housing
import pandas as pd
import os

def generate_sample_data(output_dir='data', filename='california_housing.csv'):
    """
    Generate sample California Housing dataset
    
    Args:
        output_dir: Directory to save the CSV file
        filename: Name of the output CSV file
    """
    # Fetch the dataset
    print("Fetching California Housing dataset...")
    california_housing = fetch_california_housing(as_frame=True)
    
    # Get the DataFrame
    df = california_housing.frame
    
    # Rename target column
    df = df.rename(columns={'MedHouseVal': 'MedHouseVal'})
    
    print(f"Dataset shape: {df.shape}")
    print(f"Features: {df.columns.tolist()}")
    print(f"\nFirst few rows:")
    print(df.head())
    
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    # Save to CSV
    output_path = os.path.join(output_dir, filename)
    df.to_csv(output_path, index=False)
    
    print(f"\nDataset saved to: {output_path}")
    print(f"File size: {os.path.getsize(output_path) / 1024:.2f} KB")
    
    # Print summary statistics
    print("\nSummary Statistics:")
    print(df.describe())
    
    return df, output_path

if __name__ == '__main__':
    df, output_path = generate_sample_data()
    print(f"\n✅ Sample data generated successfully!")
    print(f"Use this file for testing: {output_path}")

