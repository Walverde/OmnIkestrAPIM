# OmnIkestrAPIM — Architecture

**Status:** Draft
**Version:** 0.1
**Last Updated:** 2026-09-30

---

# 1. Overview

**OmnIkestrAPIM** is a self-hosted platform for managing products, media, marketplace listings, automation, analytics, and AI-assisted commerce workflows.

The primary goal is to provide a simple workflow for users while allowing the system to perform complex operations automatically.

The platform is designed around a **filesystem-first** concept:

> The user organizes products naturally using folders and files. OmnIkestrAPIM observes this structure, indexes the content, enriches it with metadata and AI, and uses the resulting information to manage marketplace listings, automation, and analytics.

The system should be easy to install on a personal server, NAS, or cloud server using containerized deployment.

---

# 2. Core Principles

## 2.1 Filesystem-first

The filesystem is a first-class interface.

Users should be able to create and organize products using normal folders and files without being forced to manually register every product through the web interface.

Example:

```text
Products/
└── Dell Latitude 5420/
    ├── product.yaml
    ├── images/
    │   ├── 001.jpg
    │   ├── 002.jpg
    │   └── 003.jpg
    ├── videos/
    └── documents/
```

OmnIkestrAPIM detects and indexes these changes automatically.

---

## 2.2 Database as an index and operational store

The database should not become the only source of product information.

The filesystem contains the user's actual product assets.

The database provides:

* fast queries;
* indexing;
* relationships;
* marketplace state;
* analytics;
* logs;
* task status;
* historical information.

If the database is lost, OmnIkestrAPIM should be capable of rebuilding the basic product catalog by scanning the filesystem.

---

## 2.3 API-first

All major functionality should be accessible through an API.

The web interface and mobile application should consume the same API used by internal services.

Business logic should remain in the backend rather than being duplicated across clients.

---

## 2.4 Modular architecture

Marketplace integrations, AI providers, media processors, notification systems, and analytics components should be modular.

Adding a new integration should not require modifying the entire application.

---

## 2.5 Self-hosted

OmnIkestrAPIM is designed primarily as a self-hosted application.

The user should retain control over:

* product data;
* images;
* videos;
* credentials;
* database;
* AI configuration;
* backups.

---

## 2.6 Containerized deployment

The complete application should be deployable using Docker.

The installation experience should aim to be similar to modern self-hosted applications such as Immich.

The long-term goal is to make installation possible with a simple deployment process such as:

```bash
docker compose up -d
```

after initial configuration.

---

# 3. High-Level Architecture

```text
                         ┌─────────────────────┐
                         │     Mobile App      │
                         └──────────┬──────────┘
                                    │
                                    │ HTTPS / API
                                    │
                         ┌──────────▼──────────┐
                         │      Web App        │
                         │     Dashboard       │
                         └──────────┬──────────┘
                                    │
                                    │ REST API
                                    │
                         ┌──────────▼──────────┐
                         │      Backend        │
                         │   Core Application  │
                         └──────────┬──────────┘
                                    │
             ┌──────────────────────┼──────────────────────┐
             │                      │                      │
             ▼                      ▼                      ▼
       Product Service        Marketplace Service      AI Service
             │                      │                      │
             │                      │                      │
             ▼                      ▼                      ▼
       File Watcher          Marketplace APIs       AI Providers
             │
             ▼
      Product Filesystem
             │
             ▼
           SMB/NAS
             │
             ▼
           TrueNAS

             ┌──────────────────────────────────────────┐
             │                 Storage                  │
             │                                          │
             │   Product Files + Database + Backups     │
             └──────────────────────────────────────────┘
```

---

# 4. Main Components

## 4.1 Web Application

The web application provides the primary management interface.

Responsibilities:

* dashboard;
* product management;
* media management;
* marketplace management;
* publication queue;
* analytics;
* AI tools;
* system settings;
* logs;
* user management.

The web application must not contain core business logic.

It communicates with the backend through the API.

---

# 4.2 Mobile Application

The mobile application provides a simplified interface for field operations.

Primary use cases:

* photograph a new product;
* upload images;
* create a product;
* scan barcodes;
* record voice notes;
* review product information;
* receive notifications;
* check publication status.

Example workflow:

```text
New Product
     │
     ▼
Take Photos
     │
     ▼
Upload
     │
     ▼
Create Product
     │
     ▼
AI Processing
     │
     ▼
Product Ready
```

---

# 4.3 Backend

The backend is the core of OmnIkestrAPIM.

Responsibilities include:

* authentication;
* product management;
* filesystem indexing;
* marketplace management;
* AI orchestration;
* task management;
* analytics;
* notifications;
* configuration;
* audit logging.

The backend should expose a documented API.

---

# 4.4 File Watcher

The File Watcher monitors configured product directories.

It detects events such as:

* new folders;
* deleted folders;
* renamed folders;
* new files;
* modified files;
* deleted files.

Example:

```text
Products/
└── New Product/
```

The watcher detects:

```text
ProductDirectoryCreated
```

The system then creates or updates the corresponding product record.

---

# 4.5 Product Service

The Product Service manages product information.

A product may contain:

* SKU;
* title;
* description;
* cost;
* price;
* category;
* brand;
* model;
* condition;
* tags;
* dimensions;
* weight;
* images;
* videos;
* documents;
* marketplace listings.

---

# 4.6 Media Service

The Media Service manages product images and videos.

Possible operations include:

* thumbnail generation;
* image resizing;
* format conversion;
* metadata extraction;
* duplicate detection;
* image quality analysis;
* OCR;
* background processing;
* image optimization.

Original files must never be modified destructively without explicit user authorization.

---

# 4.7 AI Service

The AI layer provides optional intelligent functionality.

Possible capabilities:

* title generation;
* description generation;
* category suggestions;
* product identification;
* specification extraction;
* OCR interpretation;
* translation;
* keyword generation;
* pricing suggestions;
* listing optimization;
* market analysis;
* sales projections.

The AI layer should support multiple providers.

Possible architecture:

```text
AI Provider
├── Local
│   └── Ollama
│
└── Cloud
    ├── Provider A
    ├── Provider B
    └── Provider C
```

The core application should not depend on a single AI provider.

---

# 4.8 Marketplace Service

Marketplace integrations are implemented as independent modules.

Example:

```text
marketplaces/
├── mercadolivre/
├── olx/
├── amazon/
└── ...
```

Each integration should expose a common interface.

Conceptually:

```python
publish()
update()
pause()
resume()
delete()
get()
sync()
```

The actual implementation depends on the capabilities and official APIs of each marketplace.

Where official APIs are unavailable, alternative mechanisms must be evaluated individually while respecting the platform's terms and technical restrictions.

---

# 4.9 Task Queue

Long-running operations should not block the API.

Examples:

* image processing;
* AI generation;
* marketplace publication;
* synchronization;
* analytics;
* market research.

Instead:

```text
API Request
     │
     ▼
Create Task
     │
     ▼
Queue
     │
     ▼
Worker
     │
     ▼
Execute
     │
     ▼
Update Status
```

---

# 4.10 Analytics

Analytics collects operational and marketplace data.

Possible metrics:

* views;
* favorites;
* inquiries;
* sales;
* revenue;
* profit;
* average selling price;
* time to sale;
* price history;
* listing performance;
* marketplace performance.

Historical data should be retained to allow comparisons over time.

---

# 4.11 Market Intelligence

Market Intelligence is responsible for transforming marketplace data into actionable information.

Possible capabilities:

* competitor price analysis;
* average market price;
* minimum and maximum observed price;
* price positioning;
* market saturation;
* historical price trends;
* product demand indicators;
* estimated time to sale;
* price elasticity analysis;
* seasonal patterns;
* recommended purchase price;
* recommended selling price.

The system should distinguish clearly between:

**Observed data**

and

**AI-generated estimates or projections.**

Predictions must never be presented as guaranteed results.

---

# 4.12 Pricing Engine

The Pricing Engine combines:

* product acquisition cost;
* desired margin;
* marketplace fees;
* shipping costs;
* competitor prices;
* historical sales;
* market conditions.

It may generate recommendations such as:

```text
Acquisition cost: R$ 2,000

Recommended listing price:
R$ 2,799

Expected margin:
R$ 799

Market position:
Above average

Recommendation:
Competitive
```

The final pricing decision remains under user control unless automatic pricing is explicitly enabled.

---

# 4.13 Notification Service

The system should be able to notify users about events.

Examples:

```text
Product published successfully.

Marketplace publication failed.

Product has been inactive for 30 days.

Price recommendation available.

Stock reached zero.

Marketplace authentication expired.
```

Possible notification channels:

* Web notifications;
* Email;
* Telegram;
* Mobile push notifications.

---

# 5. Data Architecture

The system will use a relational database for operational information.

Initial database candidate:

```text
PostgreSQL
```

Potential entities:

```text
User
Workspace
Product
ProductImage
ProductVideo
ProductDocument
Marketplace
Listing
ListingEvent
PriceHistory
Sale
Task
AIRequest
MarketObservation
PriceRecommendation
Notification
AuditLog
```

Relationships will evolve as the project develops.

---

# 6. Workspace Concept

OmnIkestrAPIM should support multiple workspaces.

Example:

```text
Workspace: Personal

Workspace: Electronics

Workspace: Used Equipment

Workspace: Company Store
```

Each workspace may have independent:

* products;
* marketplaces;
* pricing rules;
* users;
* automation rules;
* AI settings;
* watched directories.

This provides a path toward multi-business usage without requiring a separate installation for every business.

---

# 7. Product Filesystem

The filesystem structure is configurable.

A default structure may be:

```text
Products/
├── Pending/
├── Published/
├── Sold/
├── Error/
└── Archived/
```

Product directories:

```text
Products/Pending/Dell Latitude 5420/
├── product.yaml
├── images/
├── videos/
└── documents/
```

The system should avoid requiring a specific filesystem layout when possible.

Users should be able to configure watched directories.

---

# 8. Product Metadata

The preferred human-editable metadata format is YAML.

Example:

```yaml
sku: DELL-5420-001

title: Dell Latitude 5420

price: 2899.00

cost: 2200.00

condition: used

brand: Dell

model: Latitude 5420

description: |
  Dell Latitude 5420 notebook.

tags:
  - dell
  - latitude
  - notebook

marketplaces:
  mercadolivre:
    enabled: true

  olx:
    enabled: true
```

The schema must evolve carefully to maintain backwards compatibility.

---

# 9. Storage Architecture

OmnIkestrAPIM separates application data from user media.

Example:

```text
/data
├── database/
├── config/
├── cache/
└── logs/

/media
└── products/
```

The actual product media may reside on:

* local storage;
* SMB share;
* NFS;
* NAS;
* dedicated storage volume.

TrueNAS is an intended deployment target.

---

# 10. Docker Architecture

The initial deployment may contain:

```text
omnikestrapim
│
├── frontend
├── backend
├── worker
├── scheduler
├── postgres
└── redis
```

Optional services may be added later:

```text
├── ai
├── ollama
├── reverse-proxy
└── monitoring
```

The exact container structure should remain as simple as possible.

A single installation should not require unnecessary services.

---

# 11. Security Architecture

Security is a fundamental requirement.

The system should provide:

* authenticated access;
* role-based permissions;
* encrypted credentials;
* secure API tokens;
* audit logs;
* HTTPS support;
* session management;
* password hashing;
* CSRF protection where applicable;
* rate limiting;
* secure secret management.

Marketplace credentials must never be stored in plaintext.

---

# 12. Backup Strategy

The following data must be considered separately:

```text
Product Files
Database
Configuration
Secrets
Application State
```

The product filesystem should be independently backupable.

The database should have automated backup support.

A database backup alone must never be considered a complete backup of the system.

---

# 13. Event-Driven Architecture

The system should progressively adopt an event-driven architecture.

Examples:

```text
ProductCreated
ProductUpdated
MediaAdded
MediaProcessed
AIProcessingCompleted
ListingCreated
ListingUpdated
ListingPublished
ListingPublishFailed
SaleRegistered
PriceRecommendationCreated
```

Events allow independent modules to react without creating excessive dependencies between services.

Example:

```text
MediaAdded
   │
   ├── Thumbnail Worker
   ├── AI Worker
   ├── OCR Worker
   └── Analytics
```

---

# 14. API Architecture

The API should be versioned.

Example:

```text
/api/v1/products
/api/v1/listings
/api/v1/marketplaces
/api/v1/analytics
/api/v1/tasks
/api/v1/market
```

API documentation should be automatically generated where possible.

---

# 15. Deployment

The target deployment model is:

```text
Server
│
├── Docker
│
└── OmnIkestrAPIM
    │
    ├── Web
    ├── API
    ├── Workers
    ├── Database
    └── Cache
```

Installation should eventually be possible through a documented Docker Compose deployment.

The application should not depend on a specific NAS vendor.

Supported environments may include:

* TrueNAS;
* Linux servers;
* home servers;
* VPS;
* dedicated servers.

---

# 16. Development Environment

Development should be performed locally.

Recommended tools:

* Git;
* GitHub;
* VS Code;
* Docker;
* Docker Compose.

The production server should not be used as the primary development environment.

---

# 17. CI/CD

GitHub Actions should eventually provide:

```text
Pull Request
     │
     ├── Lint
     ├── Unit Tests
     ├── Integration Tests
     ├── Build
     └── Security Checks
```

On release:

```text
GitHub Release
      │
      ▼
Docker Image
      │
      ▼
Container Registry
```

This allows users to update OmnIkestrAPIM without rebuilding the application manually.

---

# 18. Observability

The system should provide enough information to diagnose problems.

Required:

* application logs;
* worker logs;
* marketplace logs;
* audit logs;
* task history;
* health checks.

A health endpoint should exist:

```text
/api/health
```

---

# 19. Current Prototype

The initial Compose deployment currently runs three services:

* **Backend:** FastAPI API and filesystem catalog scanner.
* **Watcher:** monitors the selected product directory and forwards create, delete, and move events to the backend.
* **PostgreSQL:** stores the product index and event history in the persistent `postgres_data` volume.

The host product directory is mounted at `/data/products` in both application containers. The backend mount is read-only; the watcher mount is writable. On Windows, `PRODUCTS_DIR` in the root `.env` file selects the host directory. The native folder picker at `/settings` uses `tools/windows_folder_picker.ps1` because a browser inside a container cannot enumerate arbitrary Windows paths. The helper listens on loopback, accepts requests only from the local application origins, and recreates the backend and watcher after a directory change.

The current API includes:

```text
GET  /api/health
GET  /api/products
POST /api/products/scan
GET  /api/products/events
POST /api/products/watch
GET  /settings
```

Product folders remain the source of truth. The backend scans their immediate child directories, records file summaries in PostgreSQL, and reconciles the index at startup, on manual scan, and after watcher events. SQLAlchemy creates tables at startup; schema migrations, API authentication, and production-ready secret management are not implemented yet. The default Compose database credentials are for local development only.

Example response:

```json
{
  "status": "healthy"
}
```

Future versions may add:

* Prometheus;
* Grafana;
* OpenTelemetry.

These should remain optional for small installations.

---

# 19. Development Philosophy

OmnIkestrAPIM should follow these principles.

### Keep the core simple

Do not introduce infrastructure merely because it is technically interesting.

### Prefer modularity

New integrations should be isolated.

### Prefer reversible decisions

Early architecture decisions should be easy to change.

### Automate repetitive operations

Humans should approve important decisions; machines should handle repetitive work.

### Preserve original data

Original product media should never be silently destroyed.

### Design for failure

Marketplace APIs, AI services, network connections, and storage can fail.

The system must recover gracefully.

### Make automation observable

Every automated action should have a status, history, and log.

The user must always be able to understand what the system did and why.

---

# 20. Initial Development Priorities

The first implementation should deliberately be small.

## Phase 1 — Foundation

```text
Git repository
Docker Compose
Backend
Database
Frontend
Authentication
```

## Phase 2 — Filesystem

```text
Workspace
Watched folder
File watcher
Product discovery
Product indexing
```

## Phase 3 — Product Management

```text
Product page
Images
Metadata
YAML
Search
```

## Phase 4 — Marketplace

```text
Marketplace abstraction
Mercado Livre integration
Publication queue
Listing status
```

## Phase 5 — AI

```text
AI provider abstraction
Title generation
Description generation
Category suggestions
```

## Phase 6 — Analytics

```text
Sales
Price history
Listing performance
Market analysis
```

## Phase 7 — Mobile

```text
Mobile application
Camera
Upload
Barcode
Voice input
Push notifications
```

---

# 21. Target User Experience

The final system should provide the following experience:

```text
                         USER
                           │
             ┌─────────────┴─────────────┐
             │                           │
         Mobile App                 SMB / Folder
             │                           │
             └─────────────┬─────────────┘
                           ▼
                    OmnIkestrAPIM
                           │
              ┌────────────┼────────────┐
              │            │            │
             AI        Marketplace   Analytics
              │            │            │
              └────────────┼────────────┘
                           ▼
                       Dashboard
```

The user should be able to:

1. Create or import a product.
2. Add photos naturally.
3. Let OmnIkestrAPIM detect the product.
4. Review AI-generated information.
5. Receive pricing and market recommendations.
6. Publish to selected marketplaces.
7. Monitor the listing.
8. Receive alerts about problems or opportunities.
9. Analyze sales and performance.
10. Adjust the strategy based on historical data.

The complexity should remain inside OmnIkestrAPIM.

The user's workflow should remain simple.

---

# 22. Long-Term Vision

OmnIkestrAPIM should evolve from a marketplace publishing tool into an intelligent commerce management platform.

The long-term architecture combines:

```text
Product Management
        +
Media Management
        +
Marketplace Integration
        +
Artificial Intelligence
        +
Pricing Intelligence
        +
Market Intelligence
        +
Analytics
        +
Automation
        +
Mobile
```

while maintaining the fundamental principle:

> **The system should work around the user's natural workflow, not force the user to work around the system.**

---

# 23. Architectural Evolution

This document describes the intended architecture, not a fixed implementation.

As development progresses, architectural decisions may change based on:

* performance;
* maintainability;
* security;
* deployment complexity;
* user experience;
* marketplace requirements;
* infrastructure limitations.

Significant architectural changes should be documented in the Git history and, when appropriate, in separate Architecture Decision Records (ADRs).

Future ADRs should be stored under:

```text
docs/
└── adr/
    ├── 0001-initial-architecture.md
    ├── 0002-database-selection.md
    ├── 0003-storage-strategy.md
    └── ...
```

---

# 24. Final Architectural Principle

The central idea of OmnIkestrAPIM is:

> **Files are the natural interface. APIs are the integration interface. The database is the operational index. AI is the intelligence layer. Automation is the execution layer.**

The user should not need to understand the complexity underneath the system.

They should simply be able to organize their products, and OmnIkestrAPIM should do the rest.
