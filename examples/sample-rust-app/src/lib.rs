pub fn workload(iterations: u64) -> u64 {
    workload_impl(iterations)
}

#[cfg(not(feature = "alt-impl"))]
fn workload_impl(iterations: u64) -> u64 {
    let mut acc = 0_u64;
    for i in 1..=iterations {
        acc = acc.wrapping_add(i.wrapping_mul(31));
    }
    acc
}

#[cfg(feature = "alt-impl")]
fn workload_impl(iterations: u64) -> u64 {
    let mut acc = 0_u64;
    for i in 1..=iterations {
        let mixed = i.wrapping_mul(31).rotate_left((i % 13) as u32);
        acc = acc.wrapping_add(mixed ^ i.rotate_right((i % 7) as u32));
    }
    acc
}
