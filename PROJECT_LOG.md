#  CS263 Project Log – Serverless ML Pipeline (AWS Lambda + S3 + Step Functions)

##  Project Overview
**Goal:**  
Build an end-to-end Serverless Machine Learning (ML) pipeline that automates data ingestion, cleaning, feature engineering, training, deployment, and monitoring — using AWS Lambda, S3, Step Functions, and SageMaker Serverless Inference.

**Team Members:**  
- Qi (Helen) Wu — Perm 5521497  
- Yuexi Shen - Perm A3H2V92


---

##  Decisions & Progress Summary

###  System Architecture (Finalized)
- **Core AWS Components:** Lambda, S3, Step Functions, SageMaker, CloudWatch.
- **Pipeline Flow:**  
  Data upload → Cleaning (Lambda) → Feature Engineering (Lambda) → Training (SageMaker) → Model Registration (DynamoDB) → Deployment (Serverless Endpoint) → Inference (Online + Batch) → Monitoring (CloudWatch).
- **Storage Convention:**  
  Versioned folder structure under `s3://ml-pipeline/` (raw, processed, feature_store, models, predicted, rejects).

###  Data Scope
- Example Domain: **House Price Prediction**  
- Data Type: Structured tabular (CSV/Parquet)  
- Target: `price` (regression)  
- Features: `area_sqft`, `lat`, `lon`, `built_year`, `type`, plus derived columns (`distance_to_downtown_km`, `area_sqft_sq`, etc.)
- Volume: Up to 1M records/day (batch jobs handle scalability)

- Additional Dataset (Planned): **NYC Yellow Taxi Trip Data**   
  Rationale: High-volume, real-world tabular data suitable for benchmarking serverless ETL (cleaning/feature engineering) and batch inference patterns.  
  Notes: Large monthly Parquet/CSV files; will validate schema, sampling strategy, and S3 partitioning (by `year/month`) before integration.

###  ML Framework 
- Algorithm: **XGBoost (SageMaker built-in)**  (for House Price Prediction)  
- Training trigger: Step Functions Task (`createTrainingJob`)
- Model output: `model.tar.gz` → stored in `s3://ml-pipeline/models/`

###  Deployment & Inference
- **Model Registration:** Lambda writes metadata (accuracy, RMSE, S3 URI, version) → DynamoDB.  
- **Deployment:** Lambda calls `create_model`, `create_endpoint_config`, `create_endpoint`.  
- **Serverless Inference:** Scales on demand, no idle cost.  
- **Endpoints:**  
  - Online: `POST /predict` via API Gateway → Lambda → SageMaker Endpoint  
  - Batch: S3 upload triggers EventBridge → Step Functions → Lambda → Endpoint → S3 output

###  Monitoring & Alerts 
- **Metrics:** Training accuracy, validation RMSE, model latency, 4xx/5xx errors  
- **Dashboard:** CloudWatch dashboard JSON to visualize live metrics  
- **Alerts:** CloudWatch Alarms + SNS → Slack integration for latency >2s, error >5%

---

## check table 
| Component | Status | Notes |
|------------|--------|-------|
| Data Scope & Schema | ✅ Done | California Housing dataset; housing transaction data schema defined |
| Lambda for Cleaning | ✅ Done | EventBridge-triggered Lambda cleans data (removes price=0, standardizes units), outputs to processed/ |
| Feature Engineering Lambda | ✅ Done | EventBridge-triggered Lambda builds features (distance to center, log transforms, one-hot encoding, interaction features), outputs to feature_store/ |
| Step Functions Definition | 🔵 Planned | CDK stack to be created |
| SageMaker Training Job | 🔵 Planned | XGBoost config needed |
| Model Registration + Deployment | 🔵 Planned | DynamoDB integration |
| Monitoring Dashboard | 🔵 Planned | CloudWatch metrics |
| CloudWatch Alerts + Slack | 🔵 Planned | SNS webhook |


---


