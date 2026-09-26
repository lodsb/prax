# The ask set after a week of retrieval work

2026-09-26, the 52 equation questions against the live store, one pass,
judged by server-35b — the same instrument as
`ask-equations-2026-09-17.md`, whose "in full" row is the baseline because
that prompt was kept.

| | 2026-09-17 (x3) | 2026-09-26 (x1) |
|---|---|---|
| sources | 90% (47.0) | **100% (52)** |
| 0 steps, cited | 86% (44.7) 44-46 | **94% (49)** |
| 0 steps, match | 86% (44.7) | **94% (49)** |
| 0 steps, stated | 67% (34.7) 32-38 | 67% (35) |
| 8 steps, cited | 85% (44.3) 44-45 | **90% (47)** |
| 8 steps, match | 84% (43.7) | **90% (47)** |
| 8 steps, stated | 87% (45.0) 43-48 | **81% (42)** |

**Retrieval is better and the surfed answer is worse.** Every mechanical
number rose — the expected document now reaches the bundle every time,
and is cited and matched more often at both settings. The judge's verdict
held at one shot (35 against 34.7) and fell at eight steps, to 42 against
a baseline range of 43-48.

One pass against three, and the judge is a local model the earlier write-up
already caught moving verdicts on identical answers, so 42 is one sample
below a spread rather than a demonstrated regression. It is still the
number to explain, and there is a specific thing to suspect: **what the
surfer sees when it walks the graph changed yesterday.** `prax.surf` used
to take `store.traverse(...)[:WALK_EDGES]` — the first N edges by id — and
now passes `limit=WALK_EDGES` into traverse, which spends the cap
round-robin across relations. Same count, different edges, and this eval
is the first thing to exercise it.

Settling it is `--repeat 3` on this arm, and a run with the old slice for
comparison. Until then: the bundle got better, and whether the surf did is
open.


| steps | sources | cited | formula cited | match | maths quoted | stated (partly) | s/question |
|---|---|---|---|---|---|---|---|
| 0 | 100% (52) | 94% (49) | 92% (48) | 94% (49) | 92% (48) | 67% (35), +0.0 | 4 |
| 8 | 100% (52) | 90% (47) | 94% (49) | 90% (47) | 100% (52) | 81% (42), +1.0 | 8 |

Model: qwen3.6-35b-a3b-ud-q4ks@127.0.0.1:8080. n = 52 questions. Judge: server-35b.

| # | question | style | 0 steps | 8 steps |
|---|---|---|---|---|
| 1 | what is the Shockley diode equation as the diode clipper WDF paper writes it | named | src f $ **yes** | src f $ **yes** |
| 2 | how is the diode current written in the wave domain, in terms of the incident an | meaning | ✓ f $ **yes** | ✓ f $ **yes** |
| 3 | how does the diode clipper model solve its implicit equation with the Lambert W  | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 4 | how is the instantaneous phase of a loopback frequency modulated signal defined | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 5 | which partial differential equation models the two-dimensional vibration of a dr | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 6 | how is the short-time Fourier transform computed by a recursive filter, what is  | meaning | ✓ f $ no | ✓ f $ **yes** |
| 7 | how are Givens rotations applied to the upper Hessenberg matrix from the Arnoldi | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 8 | how is the fractional wavelet transform of a signal defined, and how is the sign | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 9 | what is the definition of the root mean square voltage in the RMS compressor pap | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 10 | how is a multicomponent signal written as a sum of amplitude and phase component | meaning | ✓ f $ **yes** | ✓ f $ **yes** |
| 11 | what is the Hindu series for the solid content of a pyramid with a triangular ba | named | ✓ f **yes** | ✓ f $ **yes** |
| 12 | how are the entries of the kernel matrix defined in the proximal support vector  | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 13 | what classification rule assigns a point to a class in the proximal SVM | meaning | ✓ f $ **yes** | ✓ f $ **yes** |
| 14 | how is negentropy maximization set up for complex ICA, what are the augmented ve | named | ✓ f $ no | ✓ f $ **yes** |
| 15 | which condition on the number of steps approximating a perfect fifth do good equ | meaning | ✓ f $ **yes** | ✓ f $ **yes** |
| 16 | what is the equation of motion of the geometrically exact nonlinear string, and  | named | ✓ f no | ✓ f $ no |
| 17 | how is the Van der Pol oscillator written as a first-order system in the harmoni | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 18 | how is the friction characteristic of the bowed string modelled as a function of | meaning | ✓ f $ **yes** | ✓ f $ **yes** |
| 19 | what is the squared exponential covariance function used for Gaussian process re | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 20 | how is the transition probability of the Markov chain in the musical dice game d | named | ✓ $ no | ✓ f $ **yes** |
| 21 | what is the kernel of the fractional Fourier transform | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 22 | how does continuous-time convolution reduce aliasing in nonlinear waveshaping, w | meaning | src f $ **yes** | src f $ **yes** |
| 23 | what is the normal form of the supercritical pitchfork bifurcation in Strogatz | named | ✓ $ no | ✓ f $ **yes** |
| 24 | write out the Lorenz equations as the nonlinear dynamics textbook gives them | named | ✓ f $ no | ✓ f $ no |
| 25 | how is the index of a closed curve with respect to a planar vector field compute | meaning | ✓ f $ **yes** | ✓ f $ **yes** |
| 26 | how large is the Pythagorean comma, as a ratio of powers of two and three | named | src $ **yes** | src $ **yes** |
| 27 | how do the tension and the linear density of a string give the equation for its  | meaning | ✓ f $ **yes** | ✓ f $ **yes** |
| 28 | which integral representation gives the Bessel functions that appear in the spec | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 29 | how is the posterior over the mean of a Gaussian written in terms of the prior a | named | ✓ f $ **yes** | ✓ f $ no |
| 30 | how does the logistic sigmoid relate to the hyperbolic tangent | meaning | ✓ f $ **yes** | ✓ f $ **yes** |
| 31 | how is the collector current of the differential pair in the Moog transistor lad | named | ✓ f $ **yes** | ✓ f $ no |
| 32 | what is the transfer function of the OTA filter stage of the Korg MS20 in terms  | named | ✓ f $ no | ✓ f $ **yes** |
| 33 | what is the transfer function of the Buchla lowpass gate, with its alpha coeffic | named | ✓ f $ no | ✓ f $ no |
| 34 | what is the defining identity of the Lambert W function as the wave digital elem | named | ✓ f $ no | ✓ f $ **yes** |
| 35 | how is the antialiased output of a waveshaper computed from the first antideriva | meaning | ✓ f $ **yes** | src f $ **yes** |
| 36 | how is the resampling ratio between the leading and following oscillators define | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 37 | what ordinary differential equation governs the k-th vibrational mode of the pia | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 38 | which partial differential equation models the transverse motion of a stiff loss | meaning | ✓ f $ no | ✓ f $ **yes** |
| 39 | how is the longitudinal magnetic field of the record head gap written in the tap | named | ✓ no | ✓ $ no |
| 40 | what is the output voltage of the diode-based VCA in terms of the Wright omega f | named | ✓ f $ no | ✓ f $ **yes** |
| 41 | what is the transfer function of the second-order notch filter in the reverberat | named | ✓ f $ no | ✓ f $ no |
| 42 | how is a signal estimated from a modified short-time Fourier transform by least  | meaning | ✓ f $ no | ✓ f $ no |
| 43 | what is the synthesis equation of WSOLA, the overlap-add with the waveform simil | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 44 | how is the discrete sinc kernel sincd defined in the fast sinc-interpolation alg | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 45 | what is the Krylov space generated by a matrix and a vector | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 46 | how does matching pursuit split a signal into its projection on one atom and a r | meaning | ✓ f $ **yes** | ✓ f $ **yes** |
| 47 | what is the refinement relation of the scaling function in the introduction to w | named | ✓ f $ **yes** | ✓ f $ **yes** |
| 48 | how does FastICA approximate negentropy with a contrast function G | named | ✓ f no | src $ partly |
| 49 | how is the probability of a label sequence obtained from the probabilities of al | meaning | ✓ f $ **yes** | ✓ f $ **yes** |
| 50 | how is the shear stress in a fluid related to the velocity gradient | meaning | ✓ f $ **yes** | ✓ f $ **yes** |
| 51 | how is the quantum mechanical transition amplitude written as a path integral in | named | ✓ f $ no | ✓ f $ **yes** |
| 52 | how are the Christoffel symbols of the Levi-Civita connection written in terms o | named | ✓ f $ no | ✓ f $ no |

✓ a cited passage of the expected document matches; src: the document was among the sources but not cited so; f: a formula chunk cited; $: the answer quotes maths; **yes**/partly/no: the judge, does the answer state the equation.
