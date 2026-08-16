use criterion::{criterion_group, criterion_main, Criterion};
use sample_rust_app::workload;
use std::hint::black_box;

fn criterion_workload(c: &mut Criterion) {
    let mut group = c.benchmark_group("workload");
    group.bench_function("small", |b| b.iter(|| workload(black_box(2_000))));
    group.bench_function("medium", |b| b.iter(|| workload(black_box(20_000))));
    group.finish();
}

criterion_group!(benches, criterion_workload);
criterion_main!(benches);
