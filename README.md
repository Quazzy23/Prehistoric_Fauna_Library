# Prehistoric Fauna Library v4.0 🦖

A scientifically accurate data pipeline and 3D asset management system for prehistoric fauna.

> **Status:** 🚧 Under Development (Backbone Phase)

## 📌 Overview
This project is a Python-based ecosystem designed to bridge the gap between paleontological data and 3D reconstruction workflows. It automates the collection of taxonomic data and organizes the production pipeline for a large-scale library of 3D models.

## 🛠 Currently Implemented (Core Backbone)

### 1. Data Acquisition & ETL
*   **Deep Wikipedia Parsing:** Robust scrapers (Python 3.13) with HTTP 429 handling and automated retries.
*   **Taxonomy Synchronization:** Automated extraction of ancestral lineages and clade hierarchies.
*   **Geochronological Matching:** Mathematical mapping of million-year scales (Ma) to specific geological stages (e.g., Maastrichtian, Campanian).

### 2. Database Architecture
*   **Relational SQLite Storage:** Centralized database containing verified species data, geological time scales, and taxonomic paths.
*   **Historical Tracking:** Logic to identify and log taxonomic shifts (e.g., species moved to new genera) for future asset migration.

### 3. Management Utilities
*   **Migration Mapping:** Generation of CSV-based "migration maps" to track scientific updates and rename assets accordingly.
*   **Data Export:** Tools to sync the SQLite database with external CSV snapshots for pipeline integration.

## 🚀 Roadmap (Future Development)
- [ ] **CLI Asset Manager:** Interactive tool for artists to claim, check, and submit 3D models.
- [ ] **3D Biometrics:** Automated calculation of mesh volume and spine length via Blender Background API.
- [ ] **Technical Validation:** Automated QC scripts to verify mesh scales, naming conventions, and file integrity.
- [ ] **Cloud Integration:** Private asset hosting with Git LFS support.

## 📁 Project Structure
- `/scripts/core`: Taxonomic scrapers and database builders.
- `/scripts/management`: Scripts for handling file migrations and library integrity.
- `/scripts/utils`: Helper utilities for database exports and reporting.
- `/database`: SQLite storage and SQL schemas.

---
*This project is currently in its infrastructure development phase.*