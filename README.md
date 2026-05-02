# Prehistoric Fauna Library (PFL)

A scientifically accurate, fully automated data pipeline and 3D asset management ecosystem for prehistoric fauna reconstruction.

> **Status:** 🚧 Under Development (Phase: Code Infrastructure & Logic)

## 🌍 The Vision
The Prehistoric Fauna Library is an unprecedented attempt to build the ultimate, highly organized, and constantly updated 3D library of prehisoric animals species. 

The ultimate goal is not just to model dinosaurs, but to create a **foundational 3D ecosystem for paleoart and paleontology**. Once the library is populated, the PFL system will allow users to effortlessly generate biome-specific or clade-specific collections. For example, filtering by the "Hell Creek Formation" or "Tyrannosauridae," and automatically assembling a perfectly scaled, scientifically rigorous 3D environment ready for posing, rendering, and research. 

Every reconstruction in this library is governed by the absolute highest standards of paleoaccuracy, driven by the latest scientific literature. This is a project where art is strictly guided by data.

## 🤝 Call for Collaborators
While the project is currently in the infrastructure and coding phase, it is built to support a large-scale, collaborative production pipeline. We are laying the groundwork for a community-driven effort. 

In the future, we will be actively seeking top-tier paleo-enthusiasts and professionals to join the team:
* **Skeletal Illustrators** (to provide accurate orthographic references)
* **3D Modelers** (to sculpt scientifically acucrate base meshes)
* **Texture Artists** (to draw beautiful and realistic models skin)
* **Riggers** (to create robust, animation-ready armatures)

If you share our passion for absolute paleoaccuracy, keep an eye on this project.

## 🏗 Global Architecture

The PFL ecosystem is divided into three main pillars. Instead of manual folder management, the project relies on a **"Top-Down" automated architecture**: scientific data dictates the production environment.

### 1. The Core (Data Backbone)
An automated pipeline that scrapes Wikipedia, extracts taxonomic lineages, maps geological time scales (Ma to ICS stages), and resolves status conflicts (e.g., Valid vs. Synonym). The result is a clean, relational **SQLite database**, which serves as the infallible scientific foundation of the project.

### 2. Management (Asset Synchronization)
Scripts that act as the "bridge" between the scientific database and the physical hard drive. They generate the **Master JSON Registry** and automatically build the hierarchical folder structure (`models/Genus/Species`). This layer handles complex scientific shifts seamlessly:
* **The Migration System:** Automatically moves folders and renames meshes inside Blender files when a species changes its genus in the scientific literature.
* **The Acrhive System:** Safely archives folders of species removed from science, and automatically resurrects them with all artist data intact if they are re-validated.

### 3. Production CLI (`pfl.py`)
The interactive command-line interface that serves as the primary tool for the production team. 
* **Artists** use it to search the Master Registry, claim available species, and submit finished work (e.g., meshes, textures, rigs).
* **Curators** use it to review submissions and approve production stages.
* The tool tracks authorship, manages cloud synchronization (via Hugging Face), and ensures that no asset breaks the strict naming and scale conventions of the library.

## 📁 Repository Structure
Currently, this repository hosts the codebase required to run the pipeline.
* `/scripts/core` — Scrapers, validators, and the SQLite database builder.
* `/scripts/management` — Asset synchronization, JSON registry generation, and automated migrations.
* `pfl.py` & `main_pipeline.py` — The primary executable interfaces.

---
*Note: The project is currently focused on backend code development. 3D assets, databases, and artist-facing workflows are either in the testing phase or excluded from version control.*