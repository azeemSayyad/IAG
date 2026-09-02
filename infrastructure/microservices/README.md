# Microservices Architecture

## Service Boundaries

### 1. API Gateway
- Route requests to appropriate services
- Authentication and rate limiting
- Request/response transformation

### 2. Auth Service
- User authentication
- JWT token management
- RBAC enforcement

### 3. Lead Service
- Lead CRUD operations
- Lead ingestion (CSV, webhook, API)
- Lead scoring

### 4. Booking Service
- Appointment management
- Slot generation and locking
- Agent assignment

### 5. AI Service
- Intent detection
- Response generation
- Prompt management
- Ollama integration

### 6. Messaging Service
- SMS sending/receiving
- Engage Clouds integration
- Queue management

### 7. Analytics Service
- KPI calculations
- Report generation
- ClickHouse integration

### 8. Notification Service
- WebSocket management
- Real-time notifications
- Redis pub/sub

## Communication Patterns

### Synchronous (HTTP/gRPC)
- Auth Service → All services (token validation)
- Lead Service → AI Service (scoring)
- Booking Service → Lead Service (lead info)

### Asynchronous (Message Queue)
- Lead Service → AI Service (outreach trigger)
- AI Service → Messaging Service (send SMS)
- Messaging Service → Notification Service (delivery status)
- Booking Service → Notification Service (booking events)

## Service Discovery
- Kubernetes DNS
- Environment variables
- Service mesh (Istio optional)
