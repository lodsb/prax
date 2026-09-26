# Did the surfer's walk get worse? No, within what can be measured

2026-09-26, stage A of the programme. `prax.surf` took
`store.traverse(...)[:WALK_EDGES]` — the first 25 edges by id — and since
2026-09-25 passes `limit=WALK_EDGES` into traverse, which spends the 25
round-robin across relations. Same count, different edges. A single run
of the ask set then put the eight-step verdict at 42 against a baseline
range of 43-48, which is the kind of number that has to be chased before
anything is built on top of it.

Three repeats, the same instrument and judge as
`ask-equations-2026-09-17.md`:

| | baseline 2026-09-17 (x3) | today (x3) |
|---|---|---|
| sources | 90% (47.0) | **100% (52.0)** |
| cited | 85% (44.3) 44-45 | 85% (44.3) 42-46 |
| formula cited | 92% (47.7) | 91% (47.3) 46-48 |
| match | 84% (43.7) | 85% (44.3) 42-46 |
| stated | 87% (45.0) 43-48 | 84% (43.7) 42-46 |

**The 42 was the bottom of a 42-46 range.** `cited` matches the baseline
to the decimal, `match` is marginally above it, and `sources` went from
90% to 100% — the expected document now reaches the bundle every time.
`stated` is 1.3 lower with ranges that overlap almost entirely.

So: no regression demonstrated, and no second arm run. What this does
**not** say is that the change cost nothing — 1.3 down is also consistent
with a small real loss hiding under a four-verdict spread. It says there
is no evidence of one, and that separating a 1.3 difference from this
judge's noise would take more runs than the question is worth while
retrieval is visibly better.

Kept for the next time the surf changes: the arm to run is `--steps 8
--repeat 3`, and 42 alone means nothing.


| steps | sources | cited | formula cited | match | maths quoted | stated (partly) | s/question |
|---|---|---|---|---|---|---|---|
| 8 | 100% (52.0) | 85% (44.3) 42–46 | 91% (47.3) 46–48 | 85% (44.3) 42–46 | 98% (51.0) 50–52 | 84% (43.7) 42–46, +1.3 | 8 |

Model: qwen3.6-35b-a3b-ud-q4ks@127.0.0.1:8080. n = 52 questions, each asked 3 times: the counts are means over the runs, the range the lowest and highest run. Judge: server-35b.

| # | question | style | 8 steps |
|---|---|---|---|
| 1 | what is the Shockley diode equation as the diode clipper WDF paper writes it | named | src f $ **2/3** |
| 2 | how is the diode current written in the wave domain, in terms of the incident an | meaning | ✓ f $ **3/3** |
| 3 | how does the diode clipper model solve its implicit equation with the Lambert W  | named | ✓ f $ **3/3** |
| 4 | how is the instantaneous phase of a loopback frequency modulated signal defined | named | ✓ f $ **3/3** |
| 5 | which partial differential equation models the two-dimensional vibration of a dr | named | ✓ f $ **3/3** |
| 6 | how is the short-time Fourier transform computed by a recursive filter, what is  | meaning | ✓ f $ **2/3** (+1) |
| 7 | how are Givens rotations applied to the upper Hessenberg matrix from the Arnoldi | named | ✓ f $ **3/3** |
| 8 | how is the fractional wavelet transform of a signal defined, and how is the sign | named | ✓ f $ **3/3** |
| 9 | what is the definition of the root mean square voltage in the RMS compressor pap | named | ✓ f $ **3/3** |
| 10 | how is a multicomponent signal written as a sum of amplitude and phase component | meaning | src f $ **3/3** |
| 11 | what is the Hindu series for the solid content of a pyramid with a triangular ba | named | ✓ f $ **3/3** |
| 12 | how are the entries of the kernel matrix defined in the proximal support vector  | named | ✓ f $ **3/3** |
| 13 | what classification rule assigns a point to a class in the proximal SVM | meaning | ✓ f $ **3/3** |
| 14 | how is negentropy maximization set up for complex ICA, what are the augmented ve | named | ✓ f $ **2/3** |
| 15 | which condition on the number of steps approximating a perfect fifth do good equ | meaning | ✓ f $ **3/3** |
| 16 | what is the equation of motion of the geometrically exact nonlinear string, and  | named | ✓ f $ **3/3** |
| 17 | how is the Van der Pol oscillator written as a first-order system in the harmoni | named | ✓ f $ **3/3** |
| 18 | how is the friction characteristic of the bowed string modelled as a function of | meaning | ✓ f $ **3/3** |
| 19 | what is the squared exponential covariance function used for Gaussian process re | named | ✓ f $ **3/3** |
| 20 | how is the transition probability of the Markov chain in the musical dice game d | named | ✓ f $ **3/3** |
| 21 | what is the kernel of the fractional Fourier transform | named | ✓ f $ **3/3** |
| 22 | how does continuous-time convolution reduce aliasing in nonlinear waveshaping, w | meaning | src f $ **3/3** |
| 23 | what is the normal form of the supercritical pitchfork bifurcation in Strogatz | named | ✓ f $ 1/3 |
| 24 | write out the Lorenz equations as the nonlinear dynamics textbook gives them | named | ✓ f 1/3 |
| 25 | how is the index of a closed curve with respect to a planar vector field compute | meaning | ✓ f $ **3/3** |
| 26 | how large is the Pythagorean comma, as a ratio of powers of two and three | named | src $ **3/3** |
| 27 | how do the tension and the linear density of a string give the equation for its  | meaning | ✓ f $ **3/3** |
| 28 | which integral representation gives the Bessel functions that appear in the spec | named | ✓ f $ **3/3** |
| 29 | how is the posterior over the mean of a Gaussian written in terms of the prior a | named | ✓ f $ **3/3** |
| 30 | how does the logistic sigmoid relate to the hyperbolic tangent | meaning | ✓ f $ **3/3** |
| 31 | how is the collector current of the differential pair in the Moog transistor lad | named | ✓ f $ **3/3** |
| 32 | what is the transfer function of the OTA filter stage of the Korg MS20 in terms  | named | ✓ f $ 1/3 |
| 33 | what is the transfer function of the Buchla lowpass gate, with its alpha coeffic | named | ✓ f $ **2/3** |
| 34 | what is the defining identity of the Lambert W function as the wave digital elem | named | ✓ f $ **3/3** |
| 35 | how is the antialiased output of a waveshaper computed from the first antideriva | meaning | src f $ **3/3** |
| 36 | how is the resampling ratio between the leading and following oscillators define | named | ✓ f $ **3/3** |
| 37 | what ordinary differential equation governs the k-th vibrational mode of the pia | named | ✓ f $ **3/3** |
| 38 | which partial differential equation models the transverse motion of a stiff loss | meaning | ✓ f $ **2/3** |
| 39 | how is the longitudinal magnetic field of the record head gap written in the tap | named | ✓ f $ 0/3 |
| 40 | what is the output voltage of the diode-based VCA in terms of the Wright omega f | named | ✓ f $ 0/3 |
| 41 | what is the transfer function of the second-order notch filter in the reverberat | named | ✓ f $ 1/3 |
| 42 | how is a signal estimated from a modified short-time Fourier transform by least  | meaning | ✓ f $ **2/3** |
| 43 | what is the synthesis equation of WSOLA, the overlap-add with the waveform simil | named | ✓ f $ **3/3** |
| 44 | how is the discrete sinc kernel sincd defined in the fast sinc-interpolation alg | named | ✓ f $ **3/3** |
| 45 | what is the Krylov space generated by a matrix and a vector | named | ✓ f $ **3/3** |
| 46 | how does matching pursuit split a signal into its projection on one atom and a r | meaning | ✓ f $ **3/3** |
| 47 | what is the refinement relation of the scaling function in the introduction to w | named | ✓ f $ **3/3** |
| 48 | how does FastICA approximate negentropy with a contrast function G | named | src $ 1/3 (+2) |
| 49 | how is the probability of a label sequence obtained from the probabilities of al | meaning | ✓ f $ **2/3** (+1) |
| 50 | how is the shear stress in a fluid related to the velocity gradient | meaning | ✓ f $ **3/3** |
| 51 | how is the quantum mechanical transition amplitude written as a path integral in | named | ✓ f $ **2/3** |
| 52 | how are the Christoffel symbols of the Levi-Civita connection written in terms o | named | ✓ f $ **2/3** |

✓ a cited passage of the expected document matches; src: the document was among the sources but not cited so; f: a formula chunk cited; $: the answer quotes maths; **yes**/partly/no: the judge, does the answer state the equation; k/N: the runs the judge said yes to, the marks those of most runs.
