# EasyProtocol Dispatch Flow

This file records the dispatch skeleton for the unified outward service.

## Dispatch Steps

1. accept outward request
2. normalize the incoming envelope
3. resolve the target language service
4. hand the request to the selected service transport
5. receive raw service response
6. normalize it into the outward response shape

## Service Boundary Rule

Dispatch should treat downstream language services as independent handling
units, not as files or modules mixed directly into `EasyProtocol`.
