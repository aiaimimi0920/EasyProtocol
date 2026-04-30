# RustProtocol Service Role

`RustProtocol` is the language-specific protocol service for Rust usage.

Its role is to handle requests that should be served through the Rust-oriented
protocol stack.

It sits behind `EasyProtocol` in the current workspace layering.

It should eventually expose normalized capability, health, error, and response
information back to `EasyProtocol`.
