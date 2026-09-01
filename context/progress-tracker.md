# Advanced Multi-Source Enterprise RAG — Progress Tracker

## Overall Status
**Current Stage:** Phase 0 — Project Foundation

## Status Legend
- [ ] Not started
- [~] In progress
- [x] Complete
- [!] Blocked

## 1. Foundation
- [x] Create Git repository
- [x] Create Python virtual environment
- [x] Create `pyproject.toml`
- [x] Configure Ruff
- [x] Configure type checking
- [x] Configure pytest
- [x] Create `.env.example`
- [x] Create FastAPI app
- [x] Create Streamlit app shell

## 2. Domain Models
- [x] Source model
- [x] SourceConfig model
- [x] RawDocument model
- [x] NormalizedDocument model
- [x] Evidence model
- [ ] QueryRequest model
- [ ] QueryResponse model
- [ ] Citation model

## 3. PDF Source
- [ ] PDF uploader
- [ ] File validation
- [ ] PDF parsing
- [ ] Page metadata
- [ ] Chunking
- [ ] Indexing
- [ ] Page citations
- [ ] PDF ingestion tests

## 4. Website Source
- [ ] URL validation
- [ ] Crawler
- [ ] Crawl limits
- [ ] Same-domain option
- [ ] Text cleaning
- [ ] URL metadata
- [ ] Indexing
- [ ] Website citation tests

## 5. CSV Source
- [ ] CSV uploader
- [ ] Schema preview
- [ ] Text-column selection
- [ ] Metadata-column selection
- [ ] Row normalization
- [ ] Indexing
- [ ] Row citations

## 6. Database Source
- [ ] SQLAlchemy adapter
- [ ] Read-only connection
- [ ] Schema discovery
- [ ] Table allowlist
- [ ] SQL generation
- [ ] SQL safety validator
- [ ] Structured result evidence
- [ ] DB citations

## 7. Embeddings / Vector Store
- [ ] Embedding interface
- [ ] Hugging Face embeddings
- [ ] FAISS adapter
- [ ] Pinecone adapter
- [ ] Metadata filtering
- [ ] Vector retrieval tests

## 8. Sentence Window Retrieval
- [ ] Sentence parser
- [ ] Window metadata
- [ ] Window retriever
- [ ] Metadata replacement
- [ ] Evaluation vs baseline

## 9. Graph Retrieval
- [ ] Graph schema
- [ ] Entity extraction
- [ ] Relationship extraction
- [ ] Graph persistence
- [ ] Graph retriever
- [ ] Multi-hop test dataset
- [ ] Graph-to-source citations

## 10. Lexical Retrieval
- [ ] BM25 index
- [ ] BM25 retriever
- [ ] Identifier query tests

## 11. Fusion
- [ ] Common retriever interface
- [ ] Evidence normalization
- [ ] De-duplication
- [ ] Reciprocal Rank Fusion
- [ ] Weighted fusion
- [ ] Fusion tests

## 12. Re-ranking
- [ ] Cross-encoder model
- [ ] Re-ranker service
- [ ] Top-N configuration
- [ ] Source diversity constraint
- [ ] Latency benchmarking
- [ ] Re-ranking evaluation

## 13. Generation
- [ ] Grounded system prompt
- [ ] Context builder
- [ ] Token-budget management
- [ ] LLM client abstraction
- [ ] Insufficient-evidence response
- [ ] Structured response contract

## 14. Citations
- [ ] Stable citation IDs
- [ ] PDF locator
- [ ] Website locator
- [ ] CSV locator
- [ ] Database locator
- [ ] Inline citation renderer
- [ ] Source inspector
- [ ] Citation accuracy tests

## 15. Query Routing
- [ ] Rules-based router
- [ ] Retriever selection
- [ ] SQL intent detection
- [ ] Graph intent detection
- [ ] Evaluation of routing impact

## 16. Streamlit UI
- [ ] App shell
- [ ] Sidebar
- [ ] Source manager
- [ ] PDF upload
- [ ] Website form
- [ ] CSV upload
- [ ] DB form
- [ ] Chat
- [ ] Citation badges
- [ ] Source inspector
- [ ] Retrieval trace panel
- [ ] Evaluation dashboard

## 17. Evaluation
- [ ] Golden question set
- [ ] Expected source labels
- [ ] Recall@K
- [ ] Precision@K
- [ ] MRR
- [ ] NDCG
- [ ] Faithfulness
- [ ] Citation accuracy
- [ ] Latency tracking
- [ ] Cost tracking

## 18. Observability
- [ ] Trace IDs
- [ ] Structured logs
- [ ] Retriever timings
- [ ] Fusion trace
- [ ] Re-ranker trace
- [ ] LLM token usage
- [ ] User feedback capture

## 19. Security
- [ ] JWT/OIDC integration
- [ ] Workspace/tenant model
- [ ] Source authorization
- [ ] Retrieval metadata filters
- [ ] File validation
- [ ] Crawl allowlist
- [ ] Secret management
- [ ] SQL read-only enforcement
- [ ] Prompt injection mitigation

## 20. Local Deployment
- [ ] Dockerfile
- [ ] Docker Compose
- [ ] Local PostgreSQL
- [ ] Local Neo4j option
- [ ] Health checks

## 21. Azure Deployment
- [ ] Azure subscription/resource group
- [ ] Azure Container Apps or App Service
- [ ] Blob Storage
- [ ] Key Vault
- [ ] PostgreSQL
- [ ] Environment configuration
- [ ] Logging/monitoring
- [ ] Deployment workflow

## 22. Vercel Frontend — Optional
- [ ] Next.js UI
- [ ] API client
- [ ] Authentication
- [ ] Deploy to Vercel
- [ ] Connect to Azure backend

## 23. Portfolio Readiness
- [ ] Architecture diagram
- [ ] Demo dataset
- [ ] Demo script
- [ ] Screenshot set
- [ ] Recorded walkthrough
- [ ] README
- [ ] Resume project bullets
- [ ] Interview explanation

## Milestones

### Milestone A — Basic Multi-Source RAG
PDF + website + CSV + vector retrieval + citations.

Status: [ ]

### Milestone B — Advanced Retrieval
Sentence windows + graph + BM25 + fusion + re-ranking.

Status: [ ]

### Milestone C — Enterprise UX
Streamlit source manager + chat + evidence inspector + evaluation.

Status: [ ]

### Milestone D — Production Deployment
Azure persistence and deployment, optionally Vercel frontend.

Status: [ ]
