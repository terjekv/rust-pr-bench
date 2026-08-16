use iai_callgrind::{library_benchmark, library_benchmark_group, main};
use sample_rust_app::workload;
use std::hint::black_box;

#[library_benchmark]
#[bench::small(2_000)]
#[bench::medium(20_000)]
fn bench_workload(iterations: u64) -> u64 {
    workload(black_box(iterations))
}

library_benchmark_group!(name = benches; benchmarks = bench_workload);
main!(library_benchmark_groups = benches);
