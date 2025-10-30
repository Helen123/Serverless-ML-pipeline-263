"""
Unit tests for data cleaning Lambda handler
"""

import unittest
import pandas as pd
import numpy as np
import sys
import os

# Add repository root to path so we can import the lambda module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from lambdas.clean_transform.handler import clean_california_housing


class TestDataCleaning(unittest.TestCase):
	def setUp(self):
		"""Create sample California Housing data for testing"""
		# Sample data matching California Housing schema
		self.sample_data = pd.DataFrame({
			'MedInc': [8.3252, 8.3014, 7.2574, 5.6431, 3.8462],
			'HouseAge': [41.0, 21.0, 52.0, 52.0, 52.0],
			'AveRooms': [6.98412698, 6.23813708, 8.28813559, 5.81735160, 6.28185328],
			'AveBedrms': [1.02381000, 0.97188000, 1.07344633, 1.07305936, 1.08108108],
			'Population': [322.0, 2401.0, 496.0, 558.0, 565.0],
			'AveOccup': [2.55555556, 2.10984183, 2.80269058, 2.54794521, 2.18146718],
			'Latitude': [37.88, 37.86, 37.85, 37.84, 37.83],
			'Longitude': [-122.23, -122.22, -122.25, -122.25, -122.23],
			'MedHouseVal': [4.526, 3.585, 3.521, 3.413, 3.422]
		})

	def test_basic_cleaning(self):
		"""Test that basic cleaning produces a DataFrame and keeps valid rows"""
		result = clean_california_housing(self.sample_data)
		self.assertIsInstance(result, pd.DataFrame)
		self.assertEqual(len(result), len(self.sample_data))

	def test_remove_duplicates(self):
		"""Test that duplicates are removed"""
		data_with_dups = pd.concat([self.sample_data, self.sample_data.iloc[[0]]], ignore_index=True)
		result = clean_california_housing(data_with_dups)
		self.assertEqual(len(result), len(self.sample_data))

	def test_remove_missing_values(self):
		"""Test that rows with missing values are dropped"""
		data_with_nulls = self.sample_data.copy()
		data_with_nulls.loc[0, 'MedInc'] = np.nan
		result = clean_california_housing(data_with_nulls)
		self.assertEqual(len(result), len(self.sample_data) - 1)

	def test_remove_negative_values(self):
		"""Test that negative values for positive features are removed"""
		data_with_negatives = self.sample_data.copy()
		data_with_negatives.loc[0, 'MedInc'] = -1.0
		result = clean_california_housing(data_with_negatives)
		self.assertLess(len(result), len(self.sample_data))

	def test_cap_extreme_values(self):
		"""Test that extreme AveRooms values are capped via filtering"""
		data_with_outliers = self.sample_data.copy()
		data_with_outliers.loc[0, 'AveRooms'] = 100.0  # Unreasonably high
		result = clean_california_housing(data_with_outliers)
		self.assertLessEqual(result['AveRooms'].max(), 20)

	def test_geographic_bounds(self):
		"""Test that geographic bounds for California are enforced, not positivity"""
		data_with_bad_coords = self.sample_data.copy()
		data_with_bad_coords.loc[0, 'Latitude'] = 45.0  # Outside California
		result = clean_california_housing(data_with_bad_coords)
		self.assertLess(len(result), len(self.sample_data))
		self.assertTrue(result['Latitude'].between(32.5, 42).all())
		self.assertTrue(result['Longitude'].between(-124.5, -114).all())

	def test_preserve_valid_data(self):
		"""Test that valid data is preserved"""
		result = clean_california_housing(self.sample_data)
		self.assertEqual(len(result), len(self.sample_data))
		# Same columns and index reset after cleaning
		self.assertListEqual(list(result.columns), list(self.sample_data.columns))
		self.assertEqual(result.index[0], 0)


if __name__ == '__main__':
	unittest.main()

