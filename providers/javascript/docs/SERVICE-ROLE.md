# JSProtocol Service Role

`JSProtocol` is the language-specific protocol service for JavaScript usage.

Its role is to handle requests that should be served through the
JavaScript-oriented protocol stack.

It sits behind `EasyProtocol` in the current workspace layering.

It should eventually expose normalized capability, health, error, and response
information back to `EasyProtocol`.
